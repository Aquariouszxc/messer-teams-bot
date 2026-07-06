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
import requests
import asana_client
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


def _list_reply(query):
    rows = asana_client.list_tasks()
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


def route(text):
    text = _sanitize(text)
    action, args = _rule_parse(text)
    if action == "done":
        t = asana_client.complete_task(args.get("gid"))
        return (DONE + " Marked done: " + t["name"]) if t else (WARN + " No task #" + str(args.get("gid")))
    if action == "list":
        return _list_reply(args.get("query"))
    if action == "create":
        title = args.get("title", "Untitled")
    else:
        # free-form work-log message (same style the Telegram bot accepts)
        if not _has_real_content(text):
            return (WARN + " Please describe your work in a few words. / "
                    "Vui lòng mô tả công việc cụ thể hơn.")
        title = _freeform_title(text)
    try:
        t = asana_client.create_task(title)
    except Exception as e:
        return WARN + " Could not reach Asana, try again. (" + str(e)[:80] + ")"
    return CHECK + " Created in Asana: " + title + " (#" + str(t["gid"]) + ")"


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
