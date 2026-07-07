"""FastAPI service wiring the three tools together. Run: uvicorn main:app --reload"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
import sync, digest, teams_client, telegram_client, teams_bot, nudge
from config import WEBHOOK_SECRET, TELEGRAM_CHAT_ID, MOCK

app = FastAPI(title="INDEFOL AI Bot Integration")


@app.on_event("startup")
def _start_nudge_scheduler():
    if not MOCK:
        nudge.start_scheduler(300)   # check every 5 min; per-user gating decides who gets pinged


@app.get("/nudge")
def trigger_nudge(key: str = "", force: int = 0):
    """Manual trigger for testing the friendly nudge run. Add &force=1 to bypass work hours."""
    if key != WEBHOOK_SECRET:
        return JSONResponse({"ok": False}, status_code=403)
    return {"ok": True, "result": nudge.run_nudges(force=bool(force))}

@app.get("/healthz")
def healthz():
    return {"ok": True, "mock_mode": MOCK}

@app.get("/", response_class=PlainTextResponse)
def root():
    return ("INDEFOL AI Bot Integration (Asana↔Telegram↔Teams)\n"
            f"MOCK mode: {MOCK}\n"
            "Endpoints: /telegram/{secret} (POST), /asana/webhook (POST), /digest?key=..., /healthz")

@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        return JSONResponse({"ok": False}, status_code=403)
    upd = await request.json()
    msg = upd.get("message") or {}
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id", TELEGRAM_CHAT_ID)
    reply = sync.handle_telegram_command(text)
    telegram_client.send(chat_id, reply)
    return {"ok": True}

@app.post("/asana/webhook")
async def asana_webhook(request: Request):
    # Asana handshake: echo X-Hook-Secret on first call
    hook_secret = request.headers.get("X-Hook-Secret")
    if hook_secret:
        return Response(status_code=200, headers={"X-Hook-Secret": hook_secret})
    body = await request.json()
    for ev in body.get("events", []):
        sync.handle_asana_event(ev)
    return {"ok": True}

@app.post("/teams/messages")
async def teams_messages(request: Request):
    """Azure Bot posts Teams Activities here (Approach C messaging endpoint)."""
    activity = await request.json()
    teams_bot.handle_activity(activity)
    return {"type": "message"}

@app.get("/digest")
def run_digest(key: str = ""):
    if key != WEBHOOK_SECRET:
        return JSONResponse({"ok": False}, status_code=403)
    text = digest.build_digest()
    teams_client.post("INDEFOL weekly digest", text)
    telegram_client.send(TELEGRAM_CHAT_ID, text)
    return {"ok": True, "digest": text}
