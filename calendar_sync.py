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


def _sync_one(email, key, body, m, dry):
    url_base = pc.GRAPH + "/users/" + email + "/events"
    if dry:
        act = "update" if key in m else "create"
        return act
    import requests
    if key in m:
        r = requests.patch(url_base + "/" + m[key], headers=pc._h(), json=body, timeout=20)
        if r.status_code == 404:               # event was deleted -> recreate
            m.pop(key, None)
        elif r.ok:
            return "updated"
    r = requests.post(url_base, headers=pc._h(), json=body, timeout=20)
    if r.ok:
        m[key] = r.json().get("id")
        return "created"
    return "failed:%s" % r.status_code


def sync(dry=False):
    comp = json.load(open(COMPUTED, encoding="utf-8"))
    m = _load_map()
    counts = {}
    for t in comp["tasks"]:
        email = t.get("assignee")
        if not email or "@" not in email:
            continue
        key = "%s|%s|%s" % (PROJECT_TAG, t["id"], email)
        res = _sync_one(email, key, _event_body(t), m, dry)
        counts[res] = counts.get(res, 0) + 1
    if not dry:
        _save_map(m)
    print("calendar sync (%s): %s | events tracked: %d | finish %s buffer %sd"
          % ("DRY" if dry else "live", counts, len(m), comp.get("finish"), comp.get("buffer_days")))


if __name__ == "__main__":
    sync(dry="--dry" in sys.argv)
