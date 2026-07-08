"""Friendly proactive nudges — a helpful colleague, not a nag.

Every few minutes a scheduler calls run_nudges(). For each user who has chatted with
the bot (so we have a conversation reference), it sends a warm "anything to log?" ping
ONLY if all of these hold:
  - it's work hours (Mon-Sat, configurable),
  - they have open tasks assigned,
  - they haven't logged anything in the last hour (never nag),
  - their randomized next-nudge time has arrived (15/30/45/60 min, backs off if ignored).

Design goal is a HEALTHY habit + positive reinforcement (celebrate logs, streaks),
NOT compulsion. Respecting attention is what keeps it welcome.

State is JSON files; note Railway's disk resets on redeploy (use a volume to persist).
"""
import os, json, time, random, datetime, threading
import requests
from config import DEST
import planner_client, asana_client
try:
    import projects
except Exception:
    projects = None

REFS_FILE = os.getenv("NUDGE_REFS_FILE", "conv_refs.json")
STATE_FILE = os.getenv("NUDGE_STATE_FILE", "nudge_state.json")
TZ_OFFSET = int(os.getenv("NUDGE_TZ_OFFSET", "7"))          # Vietnam = UTC+7
START_HOUR = int(os.getenv("NUDGE_START_HOUR", "8"))
END_HOUR = int(os.getenv("NUDGE_END_HOUR", "17"))
END_MIN = int(os.getenv("NUDGE_END_MIN", "30"))
WORK_DAYS = set(int(d) for d in os.getenv("NUDGE_WORK_DAYS", "0,1,2,3,4,5").split(","))  # Mon=0
# Randomized cadence (minutes). Each person gets an independent random next-ping.
GAP_MIN = int(os.getenv("NUDGE_GAP_MIN", "20"))
GAP_MAX = int(os.getenv("NUDGE_GAP_MAX", "90"))
BACKOFF_MIN = int(os.getenv("NUDGE_BACKOFF_MIN", "120"))   # after being ignored a lot
BACKOFF_MAX = int(os.getenv("NUDGE_BACKOFF_MAX", "240"))
INIT_MIN = int(os.getenv("NUDGE_INIT_MIN", "3"))           # random stagger on first enrollment
INIT_MAX = int(os.getenv("NUDGE_INIT_MAX", "40"))
MAX_PER_DAY = int(os.getenv("NUDGE_MAX_PER_DAY", "4"))     # never overload one person

MS_APP_ID = os.getenv("MICROSOFT_APP_ID", "").strip()
MS_APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "").strip()
MS_TENANT_ID = os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()

_LOCK = threading.Lock()

MESSAGES = [
    "Hey {name} 👋 anything to log from the last while? No rush if you're heads-down. / Có gì cần ghi lại không {name}? Không vội đâu nhé.",
    "Quick one — made progress on anything? A single line is enough, I'll do the rest. / Có tiến triển gì không? Chỉ một dòng thôi, phần còn lại mình lo.",
    "Buddy, finished or moved a task along? Tell me and I'll log it. If not, all good 🙂 / Xong hay làm được việc gì thì nhắn mình ghi giúp nhé.",
    "Just checking in — want me to capture anything you did? Saves you the paperwork later. / Ghé qua xíu — cần mình ghi lại gì không? Đỡ giấy tờ sau này.",
    "No pressure at all — if something got done, drop it here and I'll file it. / Không áp lực gì đâu — xong việc gì cứ gõ vào đây.",
    "You're doing great, {name}. Anything worth logging so the team sees your progress? / Bạn làm tốt lắm {name}. Có gì đáng ghi để cả nhóm thấy tiến độ không?",
    "Tiny favor to future-you: log a quick update now so you don't have to remember later 😄 / Giúp 'bạn của tương lai': ghi nhanh một câu để khỏi phải nhớ sau.",
    "Knock knock 🚪 any wins to record? Even a small step counts. / Cốc cốc — có thành quả nào để ghi không? Bước nhỏ cũng tính nhé.",
    "Here to help, not to nag. If you moved something forward, I'll log it in seconds. / Mình giúp thôi chứ không hối. Làm được gì mình ghi trong vài giây.",
    "Whenever you have a sec — what did you get done? I'll keep the record tidy for you. / Khi nào rảnh — bạn làm xong gì rồi? Mình giữ hồ sơ gọn gàng cho.",
]
CELEBRATE = [
    "Nice — logged! 🎉 That's {count} today. / Ghi xong! Hôm nay là {count} rồi.",
    "🔥 {streak} day(s) in a row. You're on a roll! / {streak} ngày liên tiếp. Đỉnh!",
    "Thanks {name} — the team can see your progress now. / Cảm ơn {name} — cả nhóm thấy tiến độ của bạn rồi.",
    "Boom, recorded. 💪 Keep it up! / Đã ghi. Cứ thế phát huy nhé!",
]


def _load(path):
    try:
        with _LOCK:
            if os.path.exists(path):
                return json.load(open(path, encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save(path, data):
    try:
        with _LOCK:
            json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _hub():
    return planner_client if DEST == "planner" else asana_client


def _first_name(ref):
    n = (ref.get("name") or "").strip()
    return n or "bạn"


# ---- called by the bot on every incoming message ----
def save_ref(oid, activity):
    if not oid:
        return
    refs = _load(REFS_FILE)
    refs[oid] = {
        "serviceUrl": activity.get("serviceUrl"),
        "conversation": activity.get("conversation"),
        "bot": activity.get("recipient"),
        "user": activity.get("from"),
        "name": (activity.get("from") or {}).get("name"),
    }
    _save(REFS_FILE, refs)


def note_activity(oid):
    """User engaged -> reset the ignore counter so we don't back off on active people."""
    if not oid:
        return
    st = _load(STATE_FILE)
    s = st.setdefault(oid, {})
    s["ignored"] = 0
    _save(STATE_FILE, st)


def note_log(oid):
    """Record a log; return (count_today, streak) for a celebration line."""
    if not oid:
        return (1, 1)
    st = _load(STATE_FILE)
    s = st.setdefault(oid, {})
    now = _now_local()
    today = now.strftime("%Y-%m-%d")
    s["last_log"] = now.timestamp()
    s["ignored"] = 0
    if s.get("day") == today:
        s["count_today"] = s.get("count_today", 0) + 1
    else:
        yest = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        s["streak"] = (s.get("streak", 0) + 1) if s.get("day") == yest else 1
        s["day"] = today
        s["count_today"] = 1
    _save(STATE_FILE, st)
    return s.get("count_today", 1), s.get("streak", 1)


def celebrate(oid, name):
    """Build a celebration line after a successful log."""
    count, streak = note_log(oid)
    line = random.choice(CELEBRATE)
    return "\n" + line.replace("{count}", str(count)).replace("{streak}", str(streak)).replace("{name}", name or "")


# ---- scheduler internals ----
def _now_local():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=TZ_OFFSET)


def _in_work_hours(now):
    if now.weekday() not in WORK_DAYS:
        return False
    if now.hour < START_HOUR:
        return False
    if now.hour > END_HOUR or (now.hour == END_HOUR and now.minute > END_MIN):
        return False
    return True


def _has_open_tasks(oid):
    try:
        return any(oid in (r.get("assignees") or []) and not r.get("completed")
                   for r in _hub().list_tasks())
    except Exception:
        return False


def _schedule_next(s, ts):
    ig = s.get("ignored", 0)
    lo, hi = (BACKOFF_MIN, BACKOFF_MAX) if ig >= 3 else (GAP_MIN, GAP_MAX)
    s["next_ts"] = ts + random.randint(lo, hi) * 60   # random minutes -> unpredictable


def _bot_token():
    tenant = MS_TENANT_ID or "botframework.com"
    url = "https://login.microsoftonline.com/" + tenant + "/oauth2/v2.0/token"
    r = requests.post(url, data={"grant_type": "client_credentials", "client_id": MS_APP_ID,
        "client_secret": MS_APP_PASSWORD, "scope": "https://api.botframework.com/.default"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def _send_proactive(ref, text):
    try:
        tok = _bot_token()
        url = ref["serviceUrl"].rstrip("/") + "/v3/conversations/" + ref["conversation"]["id"] + "/activities"
        body = {"type": "message", "textFormat": "markdown", "from": ref["bot"],
                "recipient": ref["user"], "conversation": ref["conversation"], "text": text}
        r = requests.post(url, headers={"Authorization": "Bearer " + tok,
                          "Content-Type": "application/json"}, json=body, timeout=15)
        return r.ok
    except Exception:
        return False


def run_nudges(force=False):
    """Iterate users, apply gating, send friendly nudges. Returns a short summary string.
    force=True bypasses the work-hours check (for manual testing)."""
    now = _now_local()
    if not force and not _in_work_hours(now):
        return "skipped: outside work hours (" + now.strftime("%a %H:%M") + ")"
    refs = _load(REFS_FILE)
    st = _load(STATE_FILE)
    ts = now.timestamp()
    today = now.strftime("%Y-%m-%d")
    items = list(refs.items())
    random.shuffle(items)                         # random ORDER -> who varies each run
    sent = considered = 0
    for oid, ref in items:
        s = st.setdefault(oid, {})
        if "next_ts" not in s:
            if not force:                         # first time -> random stagger, not now
                s["next_ts"] = ts + random.randint(INIT_MIN, INIT_MAX) * 60
                continue
        elif s["next_ts"] > ts:                   # not due yet
            continue
        considered += 1
        if s.get("nudge_day") == today and s.get("nudge_count", 0) >= MAX_PER_DAY:   # daily cap
            _schedule_next(s, ts)
            continue
        if s.get("last_log") and ts - s["last_log"] < 3600:   # logged recently -> don't nag
            _schedule_next(s, ts)
            continue
        if projects:                        # check open tasks in THIS user's active project
            try:
                planner_client.set_active_plan(projects.plan_id_for(projects.get_project_key(oid)))
            except Exception:
                pass
        if not _has_open_tasks(oid):
            _schedule_next(s, ts)
            continue
        msg = random.choice(MESSAGES).replace("{name}", _first_name(ref))
        if _send_proactive(ref, msg):
            sent += 1
            s["ignored"] = s.get("ignored", 0) + 1
            s["last_nudge"] = ts
            if s.get("nudge_day") == today:
                s["nudge_count"] = s.get("nudge_count", 0) + 1
            else:
                s["nudge_day"], s["nudge_count"] = today, 1
        _schedule_next(s, ts)
    if projects:
        planner_client.set_active_plan(None)   # reset to default after the run
    _save(STATE_FILE, st)
    return "nudges sent: " + str(sent) + " (considered " + str(considered) + ")"


def _loop(interval_s=300):
    while True:
        time.sleep(interval_s)
        try:
            run_nudges()
        except Exception:
            pass


def start_scheduler(interval_s=300):
    """Start the background nudge loop (call once on app startup)."""
    t = threading.Thread(target=_loop, args=(interval_s,), daemon=True)
    t.start()
    return t
