"""Approach C — Teams inbound handler (scaffold).
Teams → Azure Bot (F0) → POST Activity here → parse (Claude, hybrid) → Asana → reply.

Hybrid routing: a fast rule parser handles clean commands; if ANTHROPIC_API_KEY is set,
ambiguous messages are sent to Claude to extract intent. In MOCK mode everything prints.
Full Bot-Framework reply (token exchange + POST to serviceUrl) is marked TODO for production.
"""
import os, re, requests
import asana_client
from config import MOCK

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MS_APP_ID = os.getenv("MICROSOFT_APP_ID", "").strip()
MS_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "").strip()
MS_TENANT_ID = os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()


def _rule_parse(text):
    """Deterministic first pass. Returns (action, args) or (None, {})."""
    t = text.strip()
    m = re.match(r"(?:create|new)\s+task[:\s]+(.+)", t, re.I)
    if m:
        return "create", {"title": m.group(1).strip()}
    m = re.match(r"(?:done|complete)\s+(\d+)", t, re.I)
    if m:
        return "done", {"gid": m.group(1)}
    if re.match(r"(?:list|status|show)\b", t, re.I):
        return "list", {"query": re.sub(r"^(list|status|show)\s*", "", t, flags=re.I).strip()}
    return None, {}


def _claude_parse(text):
    """Ambiguous → ask Claude to extract intent. Scaffold: only runs if a key is set."""
    if not ANTHROPIC_API_KEY:
        return None, {}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 200,
                  "system": ('Extract a task command. Reply ONLY as JSON '
                             '{"action":"create|done|list|none","title":"","gid":"","query":""}.'),
                  "messages": [{"role": "user", "content": text}]}, timeout=20)
        import json
        txt = r.json()["content"][0]["text"]
        data = json.loads(txt)
        return data.get("action"), data
    except Exception:
        return None, {}


def route(text):
    """Return a reply string after acting on Asana."""
    action, args = _rule_parse(text)
    if not action:
        action, args = _claude_parse(text)
    if action == "create":
        t = asana_client.create_task(args.get("title", "Untitled"))
        return f"✅ Created in Asana: {t['name']} (#{t['gid']})"
    if action == "done":
        t = asana_client.complete_task(args.get("gid"))
        return f"✔️ Marked done: {t['name']}" if t else f"⚠️ No task #{args.get('gid')}"
    if action == "list":
        rows = asana_client.list_tasks()
        q = (args.get("query") or "").lower()
        if q:
            rows = [r for r in rows if q in (r["name"] or "").lower()]
        return "\n".join(f"#{r['gid']} {'✅' if r.get('completed') else '⬜'} {r['name']}" for r in rows) or "No matching tasks."
    return ("I can: `create task <title>`, `done <id>`, or `list <subsystem>`. "
            "(Ambiguous messages are sent to Claude when ANTHROPIC_API_KEY is set.)")


def handle_activity(activity):
    """Bot Framework Activity handler. Returns reply text; sends it back to Teams."""
    if activity.get("type") != "message":
        return None
    text = (activity.get("text") or "").strip()
    reply = route(text)
    _send_reply(activity, reply)
    return reply


def _bot_token():
    """Client-credentials token for the Bot Connector (single-tenant bot)."""
    tenant = MS_TENANT_ID or "botframework.com"
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    r = requests.post(url, data={
        "grant_type": "client_credentials", "client_id": MS_APP_ID,
        "client_secret": MS_APP_PASSWORD, "scope": "https://api.botframework.com/.default"},
        timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _send_reply(activity, text):
    """Reply into the Teams conversation. MOCK prints; live posts via the Bot Connector."""
    if MOCK or not (MS_APP_ID and MS_APP_PASSWORD):
        print(f"[TEAMS reply] {text}")
        return
    try:
        tok = _bot_token()
        service = activity["serviceUrl"].rstrip("/")
        conv = activity["conversation"]["id"]
        reply = {"type": "message", "from": activity.get("recipient"),
                 "recipient": activity.get("from"), "text": text,
                 "conversation": activity.get("conversation")}
        aid = activity.get("id")
        url = f"{service}/v3/conversations/{conv}/activities" + (f"/{aid}" if aid else "")
        requests.post(url, headers={"Authorization": f"Bearer {tok}",
                                    "Content-Type": "application/json"}, json=reply, timeout=15)
    except Exception as e:
        print(f"[TEAMS reply FAILED] {e} :: {text}")
