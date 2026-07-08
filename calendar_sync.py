"""Option B: push the CPM computed schedule to each assignee's Outlook calendar.

Each task becomes ONE all-day event spanning start..due on the assignee's calendar, so
everyone sees their work as a block in Outlook (not a single dot on the deadline).
A persisted map (task -> eventId) means re-runs UPDATE the same event when a date
changes — no duplicates. Delete-safe: a 404 on update recreates the event.

Reuses planner_client's app-only Graph token, so it needs the SAME app to also have
**Calendars.ReadWrite (Application) + admin consent** granted. Run after regenerating
computed_*.json (single source of truth):

    python cpm.py schedule_model_h2.json --out computed_h2.json
    python calendar_sync.py            # syncs computed_h2.json -> Outlook calendars

Dry preview (no Graph calls, prints what it would do):
    python calendar_sync.py --dry
"""
import os, sys, json, datetime
import planner_client as pc

TZ = os.getenv("CAL_TZ", "SE Asia Standard Time")          # Vietnam UTC+7
COMPUTED = os.getenv("H2_COMPUTED_FILE", "computed_h2.json")
PROJECT_TAG = os.getenv("CAL_PROJECT_TAG", "H2")
MAP_FILE = "/data/cal_map.json" if os.path.isdir("/data") else "cal_map.json"


def _load_map():
    try:
        return json.load(open(MAP_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_map(m):
    try:
        json.dump(m, open(MAP_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


def _event_body(t):
    """All-day multi-day event: end.date is EXCLUSIVE, so use due + 1 day."""
    end_excl = (datetime.date.fromisoformat(t["due"]) + datetime.timedelta(days=1)).isoformat()
    tag = "critical (no slack)" if t.get("critical") else ("%dd slack" % t.get("slack", 0))
    return {
        "subject": "[%s] %s" % (PROJECT_TAG, t["name"]),
        "isAllDay": True,
        "showAs": "free",
        "start": {"dateTime": t["start"] + "T00:00:00", "timeZone": TZ},
        "end": {"dateTime": end_excl + "T00:00:00", "timeZone": TZ},
        "body": {"contentType": "text", "content":
                 "%s\n%s\nAuto-synced from the CPM schedule (single source of truth)."
                 % (t.get("section", ""), tag)},
        "categories": ["Hydrogen Mobility"],
    }


def _eid(rec):
    return rec.get("id") if isinstance(rec, dict) else rec


def _sync_one(email, key, t, body, m, dry):
    url_base = pc.GRAPH + "/users/" + email + "/events"
    if dry:
        return "update" if key in m else "create"
    import requests
    rec = m.get(key)
    if rec is not None:
        r = requests.patch(url_base + "/" + _eid(rec), headers=pc._h(), json=body, timeout=20)
        if r.status_code == 404:               # event was deleted -> recreate below
            m.pop(key, None)
        elif r.ok:
            m[key] = {"id": _eid(rec), "name": t["name"], "email": email}
            return "updated"
    r = requests.post(url_base, headers=pc._h(), json=body, timeout=20)
    if r.ok:
        m[key] = {"id": r.json().get("id"), "name": t["name"], "email": email}
        return "created"
    return "failed:%s" % r.status_code


def _norm(s):
    return " ".join((s or "").lower().split())


def update_progress(name, percent=None, completed=None):
    """Called by the bot AFTER a log: reflect progress/completion on the task's calendar
    event. Matches by task name across tracked events. No-op in MOCK / if not found."""
    if getattr(pc, "MOCK", False):
        return False
    m = _load_map()
    hit = None
    for rec in m.values():
        if isinstance(rec, dict) and _norm(rec.get("name")) == _norm(name):
            hit = rec
            break
    if not hit or not hit.get("id"):
        return False
    done = bool(completed) or (percent is not None and percent >= 100)
    base = "[%s] %s" % (PROJECT_TAG, name)
    if done:
        subject = "✅ " + base + " — DONE"
    elif percent:
        subject = "[%d%%] %s" % (int(percent), base)
    else:
        subject = base
    patch = {"subject": subject}
    if done:
        patch["categories"] = ["Hydrogen Mobility", "Completed"]
    import requests
    r = requests.patch(pc.GRAPH + "/users/" + hit["email"] + "/events/" + hit["id"],
                       headers=pc._h(), json=patch, timeout=20)
    return r.ok


def upsert_adhoc(user, name, day, completed=False):
    """Create/refresh a single all-day calendar event for an AD-HOC logged task (one created
    via the bot's `create task:`, not part of the CPM schedule). Placed on `day` (the log date).
    user = email OR directory oid — Graph /users/{user}/events accepts either. No-op in MOCK."""
    if getattr(pc, "MOCK", False) or not user:
        return False
    m = _load_map()
    key = "ADHOC|%s|%s" % (name, user)
    end_excl = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    subject = ("✅ [%s] %s — DONE" % (PROJECT_TAG, name)) if completed else "[%s] %s" % (PROJECT_TAG, name)
    body = {"subject": subject, "isAllDay": True, "showAs": "free",
            "start": {"dateTime": day + "T00:00:00", "timeZone": TZ},
            "end": {"dateTime": end_excl + "T00:00:00", "timeZone": TZ},
            "categories": ["Hydrogen Mobility"] + (["Completed"] if completed else [])}
    import requests
    rec = m.get(key)
    if rec is not None:
        r = requests.patch(pc.GRAPH + "/users/" + user + "/events/" + _eid(rec),
                           headers=pc._h(), json=body, timeout=20)
        if r.ok:
            m[key] = {"id": _eid(rec), "name": name, "email": user}
            _save_map(m)
            return True
        if r.status_code == 404:
            m.pop(key, None)
    r = requests.post(pc.GRAPH + "/users/" + user + "/events", headers=pc._h(), json=body, timeout=20)
    if r.ok:
        m[key] = {"id": r.json().get("id"), "name": name, "email": user}
        _save_map(m)
        return True
    return False


def sync(dry=False):
    comp = json.load(open(COMPUTED, encoding="utf-8"))
    m = _load_map()
    counts = {}
    for t in comp["tasks"]:
        email = t.get("assignee")
        if not email or "@" not in email:
            continue
        key = "%s|%s|%s" % (PROJECT_TAG, t["id"], email)
        res = _sync_one(email, key, t, _event_body(t), m, dry)
        counts[res] = counts.get(res, 0) + 1
    if not dry:
        _save_map(m)
    print("calendar sync (%s): %s | events tracked: %d | finish %s buffer %sd"
          % ("DRY" if dry else "live", counts, len(m), comp.get("finish"), comp.get("buffer_days")))


if __name__ == "__main__":
    sync(dry="--dry" in sys.argv)
