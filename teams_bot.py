"""Approach C — Teams inbound handler (+ shared free-form router).
Teams / Web Chat → parse message → Asana → reply.

Accepts BOTH:
  - explicit commands:  create task <t> | done <id> | list <kw>
  - free-form work-log text (same style as the Telegram bot), e.g.
    "Lắp ráp hệ thống điện, 3 tiếng, done"
so the identical message works in Telegram and Teams.

Input is sanitized + validated (ports Bug-Tracker fixes 003/004/005/008).
Uses Claude to make a concise title when ANTHROPIC_API_KEY is set; otherwise
uses the raw text. Reply goes back via the Bot Connector.
"""
import os, re, unicodedata
from datetime import date
import requests
import asana_client, planner_client
from config import DEST

def _hub():
    """Task destination: Planner if DEST=planner, else Asana. Same interface."""
    return planner_client if DEST == "planner" else asana_client


def _hub_name():
    return "Planner" if DEST == "planner" else "Asana"
from config import MOCK

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MS_APP_ID = os.getenv("MICROSOFT_APP_ID", "").strip()
MS_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "").strip()
MS_TENANT_ID = os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()

DONE = "✔️"      # ✔️
CHECK = "✅"           # ✅
BOX = "⬜"             # ⬜
WARN = "⚠️"      # ⚠️


def _rule_parse(text):
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


def _sanitize(text):
    """BUG-003: strip control / non-printing Unicode so ghost entries can't slip through."""
    return "".join(ch for ch in (text or "")
                   if ch in "\n\t" or unicodedata.category(ch)[0] != "C").strip()


_JUNK = {"null", "nil", "none", "undefined", "nan", "n/a", "na", "-", "."}

def _has_real_content(text):
    """BUG-004/005/008: reject junk tokens, and require >=2 letters (blocks '*', emoji-only, blanks)."""
    t = (text or "").strip().lower()
    if t in _JUNK:
        return False
    return len(re.sub(r"[^A-Za-zÀ-ỹ]", "", text or "")) >= 2


def _freeform_title(text):
    """Concise Asana title from a work-log sentence. Claude if key set, else raw text."""
    if ANTHROPIC_API_KEY:
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 120,
                      "system": ("You turn a worker's task update into a short Asana task "
                                 "title (max 10 words). Reply with ONLY the title, no quotes."),
                      "messages": [{"role": "user", "content": text}]}, timeout=20)
            title = r.json()["content"][0]["text"].strip().strip('"')
            if title:
                return title
        except Exception:
            pass
    return text[:120]


def _classify_task(text):
    """BUG-008/002: ask Claude if this is a REAL work task. Returns (valid, reason).
    Retries once on empty/invalid JSON. Without a key, cannot judge -> (True, '')."""
    if not ANTHROPIC_API_KEY:
        return True, ""
    import json
    prompt = ("Decide if the message is a REAL work-task update for an engineering project "
              "(assembly, testing, wiring, installation, etc.). Random characters, gibberish, "
              "greetings, or non-task chatter are NOT tasks. Reply ONLY JSON: "
              '{"valid_task": true or false, "reason": "short reason"}.')
    for _ in range(2):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 150,
                      "system": prompt,
                      "messages": [{"role": "user", "content": text}]}, timeout=20)
            raw = r.json()["content"][0]["text"].strip()
            raw = raw[raw.find("{"): raw.rfind("}") + 1]
            if not raw:
                continue
            data = json.loads(raw)
            return bool(data.get("valid_task")), data.get("reason", "")
        except Exception:
            continue
    # Claude unreachable/garbled -> don't crash, don't log junk; treat as invalid.
    return False, "Could not validate the task (parser unavailable). Please try again."


def _list_reply(query):
    rows = _hub().list_tasks()
    q = (query or "").lower()
    if q:
        rows = [r for r in rows if q in (r["name"] or "").lower()]
    if not rows:
        return "No matching tasks."
    lines = []
    for r in rows:
        mark = CHECK if r.get("completed") else BOX
        lines.append("#" + str(r["gid"]) + " " + mark + " " + r["name"])
    return "\n".join(lines)


# Person-based category (matches the Telegram bot: the OWNER's workstream, not the words).
# Map the responsible person's name -> their workstream. Extend as Teams users are mapped.
_OWNER_WS = {
    "yelin": "Development",          # IT & HML
    "phuc phillip": "Water Treatment",
    "hieu": "Electric Power",
    "nhu": "Separator + Gas Scrubber",
    "tung": "Purification",
    "linh": "Container",
    "tuong": "System Completion",
    "phuc k": "FAT",
    "quoc": "Electrolyzer",
    "john": "Delivery",
}
_OWNER_ROLE = {
    "yelin": "IT & HML",
    "phuc phillip": "Water Treatment", "hieu": "Electric Power",
    "nhu": "Separator/Scrubber", "tung": "Purification", "linh": "Container",
    "tuong": "System Completion", "phuc k": "FAT", "quoc": "Electrolyzer", "john": "Delivery",
}
def _owner_label(name):
    o = (name or "").lower()
    for key, role in _OWNER_ROLE.items():
        if key in o:
            return role
    return name or "Unassigned"


def _category(owner):
    o = (owner or "").lower()
    for key, ws in _OWNER_WS.items():
        if key in o:
            return ws
    return "Development"


# --- Owner detection from the message itself (Mem1-7 tags or names) --------------
# Each entry: trigger keys (ascii, accent-free) -> person, workstream label (task title),
# Planner bucket name (must match the imported schedule bucket), and assignee email.
_DOMAIN = "@indefolsolar.onmicrosoft.com"
OWNERS = [
    {"keys": ["mem1", "phillip"],        "name": "A. Phúc Phillip", "ws": "Water Treatment",
     "bucket": "WATER TREATMENT",              "email": "mem1.test" + _DOMAIN},
    {"keys": ["mem2", "hieu"],           "name": "A. Hiệu",         "ws": "Electric Power",
     "bucket": "ELECTRIC POWER",               "email": "mem2.test" + _DOMAIN},
    {"keys": ["mem3", "nhu"],            "name": "A. Như",          "ws": "Separator + Gas Scrubber",
     "bucket": "SEPARRATOR + GAS SCRUBBER",    "email": "mem3.test" + _DOMAIN},
    {"keys": ["mem4", "tung"],           "name": "Tùng",            "ws": "Purification",
     "bucket": "PURIFICATION",                 "email": "mem4.test" + _DOMAIN},
    {"keys": ["mem5", "linh"],           "name": "A. Linh",         "ws": "Container",
     "bucket": "CONTAINER",                    "email": "mem5.test" + _DOMAIN},
    {"keys": ["mem6", "tuong"],          "name": "C. Tường",        "ws": "System Completion",
     "bucket": "HOÀN THIỆN HỆ THỐNG",          "email": "mem6.test" + _DOMAIN},
    {"keys": ["mem7", "phuc k"],         "name": "Phúc K",          "ws": "FAT",
     "bucket": "FAT",                          "email": "mem7.test" + _DOMAIN},
    {"keys": ["quoc", "dao"],            "name": "Quốc Đào",        "ws": "Electrolyzer",
     "bucket": "ELECTROLYZER",                 "email": "lead.test" + _DOMAIN},
    {"keys": ["john"],                   "name": "A. John",         "ws": "Delivery",
     "bucket": "DELIVERY",                     "email": "pm.test" + _DOMAIN},
]


def _strip(s):
    """Lowercase + drop accents so 'Hiệu' matches 'hieu'."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def detect_owner(text):
    """Return the OWNERS entry the message is about (first match), or None.
    Matches whole words so 'lead time' won't hit and 'Mem2' will."""
    t = _strip(text)
    for o in OWNERS:
        for k in o["keys"]:
            if re.search(r"\b" + re.escape(k) + r"\b", t):
                return o
    return None


def route(text):
    text = _sanitize(text)
    action, args = _rule_parse(text)
    if action == "done":
        t = _hub().complete_task(args.get("gid"))
        return (DONE + " Marked done: " + t["name"]) if t else (WARN + " No task #" + str(args.get("gid")))
    if action == "list":
        return _list_reply(args.get("query"))
    if action == "create":
        work = args.get("title", "Untitled")
    else:
        # free-form work-log message (same style the Telegram bot accepts)
        if not _has_real_content(text):
            return (WARN + " Please describe your work in a few words. / "
                    "Vui lòng mô tả công việc cụ thể hơn.")
        # BUG-008: semantic "is this a real task?" gate (Claude)
        ok, reason = _classify_task(text)
        if not ok:
            return (WARN + " Không nhận diện được task. / Could not identify a task.\n"
                    + (reason and (reason + "\n") or "")
                    + "Vui lòng mô tả công việc cụ thể hơn. / Please describe your work more specifically.\n"
                    + 'Ví dụ / Example: "Lắp ráp hệ thống điện, 2 tiếng, done"')
        work = text
    # Build the SAME structured format the Telegram bot uses:
    #   NAME: [<Category>] (Owner - <name>) — <YYYY-MM-DD> (Teams)
    #   NOTES: the actual work text
    # Auto-assign: figure out WHO the task is about from the message (Mem1-7 / names).
    od = detect_owner(work)
    if od:
        category = od["ws"]; role_lbl = od["ws"]; who = od["name"]
        assignee_email = od["email"]; bucket_name = od["bucket"]
    else:
        owner = _hub().get_me() or "Unassigned"
        category = _category(owner); role_lbl = _owner_label(owner); who = owner
        assignee_email = None; bucket_name = None
    today = date.today().isoformat()
    name = "[" + category + "] (Owner - " + role_lbl + ") \u2014 " + today + " (Teams)"
    try:
        if DEST == "planner":
            bid = planner_client.bucket_id_for(bucket_name) if bucket_name else None
            planner_client.create_task(name, notes=work, assignee=assignee_email, bucket_id=bid)
            assigned_to = who if assignee_email else "Team (unassigned)"
        else:
            # Asana assignment is by user gid; email won't map, so keep the token owner.
            asana_client.create_task(name, notes=work, assignee="me")
            assigned_to = who
    except Exception as e:
        return WARN + " Could not reach " + _hub_name() + ", try again. (" + str(e)[:80] + ")"
    return (CHECK + " Logged to " + _hub_name() + ", assigned to " + assigned_to + "\n"
            + name + "\n\u2192 " + work[:120])


def handle_activity(activity):
    """Bot Framework Activity handler. Returns reply text; sends it back to Teams."""
    if activity.get("type") != "message":
        return None
    reply = route(activity.get("text") or "")
    _send_reply(activity, reply)
    return reply


def _bot_token():
    tenant = MS_TENANT_ID or "botframework.com"
    url = "https://login.microsoftonline.com/" + tenant + "/oauth2/v2.0/token"
    r = requests.post(url, data={
        "grant_type": "client_credentials", "client_id": MS_APP_ID,
        "client_secret": MS_APP_PASSWORD, "scope": "https://api.botframework.com/.default"},
        timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _send_reply(activity, text):
    if MOCK or not (MS_APP_ID and MS_APP_PASSWORD):
        print("[TEAMS reply] " + text)
        return
    try:
        tok = _bot_token()
        service = activity["serviceUrl"].rstrip("/")
        conv = activity["conversation"]["id"]
        aid = activity.get("id")
        url = service + "/v3/conversations/" + conv + "/activities" + ("/" + aid if aid else "")
        reply = {"type": "message", "from": activity.get("recipient"),
                 "recipient": activity.get("from"), "text": text,
                 "conversation": activity.get("conversation")}
        requests.post(url, headers={"Authorization": "Bearer " + tok,
                                    "Content-Type": "application/json"}, json=reply, timeout=15)
    except Exception as e:
        print("[TEAMS reply FAILED] " + str(e) + " :: " + text)
