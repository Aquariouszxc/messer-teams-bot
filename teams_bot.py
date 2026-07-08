"""Approach C - Teams inbound handler (+ shared free-form router).
Teams / Web Chat -> parse message -> match a PLANNED task -> confirm -> log progress.

Accepts BOTH:
  - explicit commands:  create task <t> | done <id> | list <kw>
  - free-form work-log text (same style as the Telegram bot), e.g.
    "Lap rap he thong dien, 3 tieng, done"

Flow for a free-form update:
  1. validate it is a real task (Claude gate),
  2. match it to one of the planned schedule tasks (Claude),
  3. ASK the user to confirm (yes / yes done / yes 50% / no),
  4. only then log it AS PROGRESS on that planned task (comment + percentComplete),
  or, if nothing matches / user says no, create a NEW standalone task.

Input is sanitized + validated (ports Bug-Tracker fixes 003/004/005/008).
Reply goes back via the Bot Connector.
"""
import os, re, time, unicodedata
from datetime import date
import requests
import asana_client, planner_client
from config import DEST, MOCK
try:
    import nudge
except Exception:
    nudge = None
try:
    import projects
except Exception:
    projects = None


def _hub():
    """Task destination: Planner if DEST=planner, else Asana. Same interface."""
    return planner_client if DEST == "planner" else asana_client


def _hub_name():
    return "Planner" if DEST == "planner" else "Asana"


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
MS_APP_ID = os.getenv("MICROSOFT_APP_ID", "").strip()
MS_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "").strip()
MS_TENANT_ID = os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()

DONE = "✔️"
CHECK = "✅"
BOX = "⬜"
WARN = "⚠️"
DASH = " — "
ARROW = "→ "


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
    """BUG-004/005/008: reject junk tokens, and require >=2 letters."""
    t = (text or "").strip().lower()
    if t in _JUNK:
        return False
    return len(re.sub(r"[^A-Za-zÀ-ỹ]", "", text or "")) >= 2


def _classify_task(text):
    """BUG-008/002: ask Claude if this is a REAL work task. Returns (valid, reason)."""
    if not ANTHROPIC_API_KEY:
        return True, ""
    import json
    prompt = ("Decide if the message is a REAL work update worth logging on a project. "
              "ACCEPT any genuine professional work the person did or is doing, including: "
              "engineering (assembly, testing, wiring, installation), design/CAD, planning, "
              "scheduling, project management / PMO, software or system development, IT setup, "
              "documentation, research, procurement, coordination, meetings, onboarding, and admin "
              "that moves the project forward. REJECT ONLY non-work content: random characters, "
              "gibberish, greetings, or chit-chat with no work described. When in doubt, ACCEPT. "
              'Reply ONLY JSON: {"valid_task": true or false, "reason": "short reason"}.')
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
    return False, "Could not validate the task (parser unavailable). Please try again."


def _list_reply(query):
    rows = _hub().list_tasks()
    q = (query or "").lower()
    if q:
        rows = [r for r in rows if q in (r["name"] or "").lower()]
    if not rows:
        return "No matching tasks. / Không có task phù hợp."
    lines = []
    for r in rows:
        mark = CHECK if r.get("completed") else BOX
        lines.append("#" + str(r["gid"]) + " " + mark + " " + r["name"])
    return "\n".join(lines)


# ---- Person-based category (fallback when no planned-task match) -----------------
_OWNER_WS = {
    "yelin": "Development", "phuc phillip": "Water Treatment", "hieu": "Electric Power",
    "nhu": "Separator + Gas Scrubber", "tung": "Purification", "linh": "Container",
    "tuong": "System Completion", "phuc k": "FAT", "quoc": "Electrolyzer", "john": "Delivery",
}
_OWNER_ROLE = {
    "yelin": "IT & HML", "phuc phillip": "Water Treatment", "hieu": "Electric Power",
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


# ---- Owner detection from the message (Mem1-7 tags or names) ---------------------
_DOMAIN = "@indefolsolar.onmicrosoft.com"
OWNERS = [
    {"keys": ["mem1", "phillip"], "name": "A. Phuc Phillip", "ws": "Water Treatment",
     "bucket": "WATER TREATMENT", "email": "mem1.test" + _DOMAIN},
    {"keys": ["mem2", "hieu"], "name": "A. Hieu", "ws": "Electric Power",
     "bucket": "ELECTRIC POWER", "email": "mem2.test" + _DOMAIN},
    {"keys": ["mem3", "nhu"], "name": "A. Nhu", "ws": "Separator + Gas Scrubber",
     "bucket": "SEPARRATOR + GAS SCRUBBER", "email": "mem3.test" + _DOMAIN},
    {"keys": ["mem4", "tung"], "name": "Tung", "ws": "Purification",
     "bucket": "PURIFICATION", "email": "mem4.test" + _DOMAIN},
    {"keys": ["mem5", "linh"], "name": "A. Linh", "ws": "Container",
     "bucket": "CONTAINER", "email": "mem5.test" + _DOMAIN},
    {"keys": ["mem6", "tuong"], "name": "C. Tuong", "ws": "System Completion",
     "bucket": "HOÀN THIỆN HỆ THỐNG", "email": "mem6.test" + _DOMAIN},
    {"keys": ["mem7", "phuc k"], "name": "Phuc K", "ws": "FAT",
     "bucket": "FAT", "email": "mem7.test" + _DOMAIN},
    {"keys": ["quoc", "dao"], "name": "Quoc Dao", "ws": "Electrolyzer",
     "bucket": "ELECTROLYZER", "email": "lead.test" + _DOMAIN},
    {"keys": ["john"], "name": "A. John", "ws": "Delivery",
     "bucket": "DELIVERY", "email": "pm.test" + _DOMAIN},
]


def _strip(s):
    """Lowercase + drop accents so 'Hieu' matches 'Hiệu'."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def detect_owner(text):
    """Return the OWNERS entry the message is about (first whole-word match), or None."""
    t = _strip(text)
    for o in OWNERS:
        for k in o["keys"]:
            if re.search(r"\b" + re.escape(k) + r"\b", t):
                return o
    return None


# ---- Match a chat update to a PLANNED task, then confirm before logging ----------
PENDING = {}   # per-conversation short-term memory of what we're waiting to confirm
_SEEN = {}     # activity-id -> ts, to drop duplicate deliveries from Teams retries


def _planned_candidates():
    """Planned schedule tasks only - exclude chat-log cards whose names start with '['."""
    out = []
    try:
        for r in _hub().list_tasks():
            nm = (r.get("name") or "").strip()
            if nm and not nm.startswith("["):
                out.append((r["gid"], nm))
    except Exception:
        pass
    return out


def _match_planned(work):
    """Return (gid, name) of the planned task this update is about, or None."""
    cands = _planned_candidates()
    if not cands:
        return None
    if ANTHROPIC_API_KEY:
        listing = "\n".join(str(i + 1) + ". " + c[1] for i, c in enumerate(cands))
        sysmsg = ("You match a worker's update to ONE planned engineering task from the list. "
                  "Reply ONLY the task number. Reply 0 if none is a clear match.")
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 8, "system": sysmsg,
                      "messages": [{"role": "user",
                                    "content": "UPDATE: " + work + "\n\nPLANNED TASKS:\n" + listing}]},
                timeout=20)
            raw = r.json()["content"][0]["text"].strip()
            n = int(re.sub(r"[^0-9]", "", raw) or "0")
            if 1 <= n <= len(cands):
                return cands[n - 1]
            return None
        except Exception:
            pass
    wset = set(_strip(work).split())
    best, score = None, 0
    for gid, nm in cands:
        ov = len(wset & set(_strip(nm).split()))
        if ov > score:
            best, score = (gid, nm), ov
    return best if score >= 2 else None


def _parse_confirm(text):
    """Interpret a confirmation reply -> ('yes', percent|None) | ('no', None) | ('unclear', None)."""
    t = _strip(text).strip()
    words = t.split()
    percent = None
    m = re.search(r"(\d{1,3})\s*%", t)
    if any(k in t for k in ("done", "complete", "hoan thanh", "xong")):
        percent = 100
    elif m:
        percent = min(100, int(m.group(1)))
    yes = any(w in words for w in ("yes", "y", "ok", "oke", "okay", "yeah", "yep", "co", "dung", "ung"))
    no = any(w in words for w in ("no", "n", "not", "khong", "ko", "cancel", "huy"))
    if yes and not no:
        return ("yes", percent)
    if no and not yes:
        return ("no", None)
    if percent is not None and not no:
        return ("yes", percent)
    return ("unclear", None)


def _log_new_task(work):
    """Create a NEW standalone task (owner-detected bucket + assignee). Returns reply text."""
    od = detect_owner(work)
    if od:
        category = od["ws"]; role_lbl = od["ws"]; who = od["name"]
        assignee_email = od["email"]; bucket_name = od["bucket"]
    else:
        owner = _hub().get_me() or "Unassigned"
        category = _category(owner); role_lbl = _owner_label(owner); who = owner
        assignee_email = None; bucket_name = None
    name = "[" + category + "] (Owner - " + role_lbl + ")" + DASH + date.today().isoformat() + " (Teams)"
    try:
        if DEST == "planner":
            bid = planner_client.bucket_id_for(bucket_name) if bucket_name else None
            planner_client.create_task(name, notes=work, assignee=assignee_email, bucket_id=bid)
            assigned_to = who if assignee_email else "Team (unassigned)"
        else:
            asana_client.create_task(name, notes=work, assignee="me")
            assigned_to = who
    except Exception as e:
        return (WARN + " Could not reach " + _hub_name() + ", try again. / Không kết nối được "
                + _hub_name() + ", thử lại. (" + str(e)[:80] + ")")
    return (CHECK + " New task created / Đã tạo task mới (" + _hub_name()
            + ") — assigned to / giao cho: " + assigned_to + "\n" + name + "\n" + ARROW + work[:120])


try:
    import calendar_sync
except Exception:
    calendar_sync = None


def _cal_progress(name, pct):
    """After a log, reflect progress/completion on the task's Outlook calendar event."""
    if calendar_sync:
        try:
            calendar_sync.update_progress(name, pct)
        except Exception:
            pass


def _celebrate(sender):
    if nudge and sender and sender.get("oid"):
        try:
            return nudge.celebrate(sender["oid"], (sender.get("name") or "").split(" ")[0])
        except Exception:
            return ""
    return ""


def _apply_progress(pend, percent, sender=None):
    """Log the confirmed update AS PROGRESS on the matched planned task."""
    gid, name, work = pend["gid"], pend["name"], pend["work"]
    pct = percent if percent is not None else 50   # plain 'yes' -> In Progress
    try:
        _hub().add_progress(gid, work, pct)
    except Exception as e:
        return WARN + " Could not update, try again. / Không cập nhật được, thử lại. (" + str(e)[:80] + ")"
    _cal_progress(name, pct)
    status = ("Completed / Hoàn thành" if pct >= 100
              else "In Progress / Đang thực hiện " + str(pct) + "%")
    return (CHECK + " Logged as progress on the planned task / Đã ghi tiến độ cho công việc:\n"
            + ARROW + '"' + name + '"  [' + status + "]\n" + ARROW + work[:120] + _celebrate(sender))


GEAR = "🔧"
_QWORDS = ("what", "which", "show", "list", "how many", "do you", "liet ke", "xem", "cho toi")


def _detect_query(text):
    """Detect a QUESTION about tasks (not a work update). Returns {scope,status} or None."""
    t = _strip(text)
    kw = any(k in t for k in (
        "my task", "my tasks", "assigned to me", "overdue", "past due", "late task",
        "qua han", "tre han", "status", "tinh trang", "unfinished", "not done", "remaining",
        "con lai", "chua xong", "in progress", "dang lam", "dang thuc hien", "finished",
        "completed", "hoan thanh", "not started", "chua bat dau", "cua toi", "viec cua toi",
        "my progress", "outstanding", "to do", "todo"))
    task_ctx = any(k in t for k in ("task", "cong viec", "viec", "assignment", "to do", "todo"))
    has_q = ("?" in text) or any(t.startswith(w) or (" " + w) in t for w in _QWORDS)
    if not (kw or (has_q and task_ctx)):
        return None
    scope = "all" if any(k in t for k in
                         ("all task", "team", "project", "everyone", "tat ca", "toan bo", "whole")) else "mine"
    if any(k in t for k in ("overdue", "past due", "late", "qua han", "tre han")):
        status = "overdue"
    elif any(k in t for k in ("not started", "chua bat dau")):
        status = "notstarted"
    elif any(k in t for k in ("in progress", "dang lam", "dang thuc hien", "ongoing")):
        status = "inprogress"
    elif any(k in t for k in ("unfinished", "not done", "open task", "remaining", "con lai",
                              "chua xong", "left", "outstanding")):
        status = "open"
    elif any(k in t for k in ("finished", "completed", "complete", "hoan thanh", "da xong")):
        status = "completed"
    else:
        status = "all"
    return {"scope": scope, "status": status}


def _status_of(r, today):
    if r.get("completed"):
        return "completed"
    if r.get("due") and r["due"] < today:
        return "overdue"
    return "inprogress" if (r.get("percent") or 0) >= 1 else "notstarted"


def _query_reply(f, sender):
    try:
        rows = _hub().list_tasks()
    except Exception as e:
        return WARN + " Could not read tasks. / Không đọc được danh sách. (" + str(e)[:60] + ")"
    who = "everyone / mọi người"
    if f["scope"] == "mine":
        oid = sender and sender.get("oid")
        who = (sender and sender.get("name")) or "you / bạn"
        if DEST != "planner":
            return (WARN + " 'My tasks' works on Planner. / 'Việc của tôi' chạy trên Planner.")
        if not oid:
            return (WARN + " I couldn't identify you. Message me in the 1:1 bot chat. / "
                    "Không xác định được bạn.")
        rows = [r for r in rows if oid in (r.get("assignees") or [])]
    today = date.today().isoformat()
    from collections import Counter
    cnt = Counter(_status_of(r, today) for r in rows)
    st = f["status"]

    def keep(r):
        s = _status_of(r, today)
        if st == "all":
            return True
        if st == "open":
            return s != "completed"
        return s == st
    sel = [r for r in rows if keep(r)]
    # Markdown so Teams renders real line breaks: bold headers + bullet lists, grouped by status.
    out = ["📋 **Tasks for " + who + "**  ·  " + str(len(sel)) + " shown / hiển thị",
           ("**Overdue/Quá hạn:** %d  ·  **In progress/Đang làm:** %d  ·  "
            "**Not started/Chưa:** %d  ·  **Done/Xong:** %d")
           % (cnt.get("overdue", 0), cnt.get("inprogress", 0),
              cnt.get("notstarted", 0), cnt.get("completed", 0))]
    if not sel:
        out.append("_No tasks match. / Không có công việc phù hợp._")
        return "\n\n".join(out)
    try:  # bucket_id -> section name, so ambiguous task names show their phase
        bmap = {b["id"]: b.get("name") for b in _hub().list_buckets()}
    except Exception:
        bmap = {}
    groups = [("overdue", WARN + " Overdue / Quá hạn"),
              ("inprogress", GEAR + " In progress / Đang làm"),
              ("notstarted", BOX + " Not started / Chưa bắt đầu"),
              ("completed", CHECK + " Done / Đã xong")]
    for skey, title in groups:
        items = sorted([r for r in sel if _status_of(r, today) == skey],
                       key=lambda r: r.get("due") or "9999")
        if not items:
            continue
        block = ["**" + title + " (" + str(len(items)) + ")**"]
        for r in items[:15]:
            meta = "  ·  ".join(x for x in (bmap.get(r.get("bucket_id")), r.get("due")) if x)
            block.append("- " + (r.get("name") or "") + (("  ·  " + meta) if meta else ""))
        if len(items) > 15:
            block.append("- _… +" + str(len(items) - 15) + " more_")
        out.append("\n".join(block))
    return "\n\n".join(out)


def _detect_log_intent(text):
    """User wants to record a contribution but hasn't named the work -> start the wizard."""
    t = _strip(text)
    return any(k in t for k in (
        "help me log", "log my", "want to log", "i want to log", "log it", "log the task",
        "log a task", "log this", "record my", "record it", "log progress", "log contribution",
        "update progress", "cap nhat tien do", "ghi nhan", "muon log", "contributed", "contribution",
        "report my progress", "help me record", "log for me", "help me update"))


def _start_wizard(conv_key, sender):
    if DEST != "planner":
        return WARN + " This works on Planner. / Chức năng này chạy trên Planner."
    oid = sender and sender.get("oid")
    if not oid:
        return (WARN + " I can't identify you — use the 1:1 bot chat. / "
                "Không xác định được bạn — dùng chat riêng với bot.")
    try:
        rows = _hub().list_tasks()
    except Exception as e:
        return WARN + " Could not read tasks. / Không đọc được. (" + str(e)[:50] + ")"
    mine = [r for r in rows if oid in (r.get("assignees") or []) and not r.get("completed")]
    if not mine:
        return CHECK + " You have no open tasks to log. / Bạn không có công việc nào để ghi."
    mine = sorted(mine, key=lambda r: r.get("due") or "9999")[:15]
    cands = [(r["gid"], r.get("name") or "") for r in mine]
    PENDING[conv_key] = {"kind": "wizard", "step": "pick", "cands": cands}
    lines = [WARN + " Which task do you want to log? Reply with the number. / "
             "Bạn muốn ghi cho công việc nào? Trả lời số:"]
    for i, (gid, name) in enumerate(cands, 1):
        lines.append(str(i) + ". " + name)
    return "\n".join(lines)


def _wizard_step(pend, text, conv_key, sender=None):
    t = _strip(text)
    if any(w in t.split() for w in ("cancel", "huy", "stop", "thoat")):
        PENDING.pop(conv_key, None)
        return "Okay, cancelled. / Đã huỷ."
    if pend["step"] == "pick":
        cands = pend["cands"]
        m = re.search(r"\d+", t)
        idx = None
        if m:
            idx = int(m.group()) - 1
        else:
            for i, (gid, name) in enumerate(cands):
                sn = _strip(name)
                if len(t) >= 3 and (t in sn or sn in t):
                    idx = i
                    break
        if idx is None or not (0 <= idx < len(cands)):
            return (WARN + " Reply with a task number from the list. / "
                    "Trả lời bằng số trong danh sách.")
        pend["task"] = cands[idx]
        pend["step"] = "describe"
        PENDING[conv_key] = pend
        return (CHECK + ' Task: "' + cands[idx][1] + '"\n'
                "Now tell me what you did and % complete (e.g. 'installed pump, 50%'). / "
                "Hãy mô tả việc bạn làm và % (vd 'lắp bơm, 50%').")
    # step == "describe"
    gid, name = pend["task"]
    mp = re.search(r"(\d{1,3})\s*%", t)
    if any(k in t for k in ("done", "complete", "hoan thanh", "xong")):
        pct = 100
    elif mp:
        pct = min(100, int(mp.group(1)))
    else:
        pct = 50
    PENDING.pop(conv_key, None)
    try:
        _hub().add_progress(gid, text, pct)
    except Exception as e:
        return WARN + " Could not update. / Không cập nhật được. (" + str(e)[:60] + ")"
    _cal_progress(name, pct)
    status = "Completed / Hoàn thành" if pct >= 100 else "In Progress / Đang thực hiện " + str(pct) + "%"
    return (CHECK + " Logged as progress / Đã ghi tiến độ:\n" + ARROW + '"' + name + '"  [' + status + "]\n"
            + ARROW + text[:120] + _celebrate(sender))


def route(text, conv_key="default", sender=None):
    text = _sanitize(text)

    # (0) project switch: "/project h2" | "/project messer"
    if projects:
        mm = re.match(r"^/project\b\s*(\w+)?", text.strip(), re.I)
        if mm:
            oid = sender and sender.get("oid")
            key = projects.resolve_alias(mm.group(1)) if mm.group(1) else None
            if key:
                projects.set_project(oid, key)
                planner_client.set_active_plan(projects.plan_id_for(key))
                PENDING.pop(conv_key, None)
                return (CHECK + " Switched to project: " + projects.name_for(key)
                        + " / Đã chuyển sang dự án: " + projects.name_for(key))
            cur = projects.name_for(projects.get_project_key(oid)) if oid else "?"
            return (WARN + " Current project: " + cur
                    + ". Use  /project h2  or  /project messer.  / Dự án hiện tại: " + cur + ".")

    # (A) Waiting on a yes/no confirmation from this conversation?
    pend = PENDING.get(conv_key)
    if pend:
        if pend.get("kind") == "wizard":
            return _wizard_step(pend, text, conv_key, sender)
        verdict, percent = _parse_confirm(text)
        if verdict == "yes":
            PENDING.pop(conv_key, None)
            return _apply_progress(pend, percent, sender) if pend["kind"] == "link" else _log_new_task(pend["work"])
        if verdict == "no":
            PENDING.pop(conv_key, None)
            if pend["kind"] == "link":
                PENDING[conv_key] = {"kind": "new", "work": pend["work"]}
                return (WARN + " Not that task. Log it as a NEW task instead? (yes/no)\n"
                        "/ Không phải task đó. Ghi thành task mới? (yes/no)")
            return "Okay, cancelled - nothing logged. / Đã huỷ, không ghi gì."
        return (WARN + " Please reply yes or no"
                + (pend["kind"] == "link" and " (or 'yes done' / 'yes 50%')" or "")
                + ".\n/ Vui lòng trả lời yes hoặc no.")

    # (B) explicit commands still work
    action, args = _rule_parse(text)
    if action == "done":
        t = _hub().complete_task(args.get("gid"))
        return (DONE + " Marked done / Đã hoàn thành: " + t["name"]) if t else (WARN + " No task / Không thấy task #" + str(args.get("gid")))
    if action == "list":
        return _list_reply(args.get("query"))
    if action == "create":
        return _log_new_task(args.get("title", "Untitled"))

    # (B1) "help me log / record my contribution" -> guided pick-a-task wizard
    if _detect_log_intent(text):
        return _start_wizard(conv_key, sender)

    # (B2) natural-language QUESTION about tasks (my tasks / overdue / status / ...)
    qf = _detect_query(text)
    if qf:
        return _query_reply(qf, sender)

    # (C) free-form work-log update -> validate -> match planned task -> CONFIRM first
    if not _has_real_content(text):
        return (WARN + " Please describe your work in a few words. / "
                "Vui lòng mô tả công việc cụ thể hơn.")
    ok, reason = _classify_task(text)
    if not ok:
        return (WARN + " Could not identify a task. / Không nhận diện được task.\n"
                + (reason and (reason + "\n") or "")
                + "Please describe your work more specifically. / Vui lòng mô tả công việc cụ thể hơn.\n"
                + 'Example / Ví dụ: "Lắp ráp hệ thống điện, 2 tiếng, done"')
    work = text
    m = _match_planned(work)
    if m:
        PENDING[conv_key] = {"kind": "link", "gid": m[0], "name": m[1], "work": work}
        return (WARN + " Is this an update to the planned task:\n" + ARROW + '"' + m[1] + '" ?\n'
                "Reply  yes  (or 'yes done' / 'yes 50%') to log it, or  no.\n"
                "/ Đây là cập nhật cho công việc trên? Trả lời yes / yes done / yes 50% / no.")
    PENDING[conv_key] = {"kind": "new", "work": work}
    return (WARN + " This doesn't match a planned task. Log it as a NEW task? (yes/no)\n"
            "/ Không khớp công việc kế hoạch nào. Ghi thành task mới? (yes/no)")


def handle_activity(activity):
    """Bot Framework Activity handler. Returns reply text; sends it back to Teams."""
    if activity.get("type") != "message":
        return None
    aid = activity.get("id")   # drop duplicate deliveries (Teams retries the same activity id)
    if aid:
        now = time.time()
        for k in [k for k, t in _SEEN.items() if now - t > 120]:
            _SEEN.pop(k, None)
        if aid in _SEEN:
            return None
        _SEEN[aid] = now
    frm = activity.get("from", {}) or {}
    conv_key = (activity.get("conversation", {}) or {}).get("id") or frm.get("id") or "default"
    sender = {"oid": frm.get("aadObjectId"), "name": frm.get("name")}
    if nudge and sender["oid"]:   # remember how to reach this user + they're active now
        nudge.save_ref(sender["oid"], activity)
        nudge.note_activity(sender["oid"])
    if projects:                  # route this message to the user's active project's plan
        try:
            planner_client.set_active_plan(projects.plan_id_for(projects.get_project_key(sender["oid"])))
        except Exception:
            pass
    try:
        reply = route(activity.get("text") or "", conv_key, sender)
    finally:
        if projects:
            planner_client.set_active_plan(None)   # reset to default (Messer) after handling
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
        reply = {"type": "message", "textFormat": "markdown",
                 "from": activity.get("recipient"),
                 "recipient": activity.get("from"), "text": text,
                 "conversation": activity.get("conversation")}
        requests.post(url, headers={"Authorization": "Bearer " + tok,
                                    "Content-Type": "application/json"}, json=reply, timeout=15)
    except Exception as e:
        print("[TEAMS reply FAILED] " + str(e) + " :: " + text)
