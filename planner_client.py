"""Microsoft Planner via Graph (app-only) — mirrors asana_client's interface so the bot
can write to Planner instead of Asana by setting DEST=planner.

Uses the SAME app credentials as the bot (MICROSOFT_APP_ID/PASSWORD/TENANT), requesting a
Graph token (scope https://graph.microsoft.com/.default). Requires the app to have the Graph
application permission Tasks.ReadWrite.All with admin consent.

NOTE: Planner app-only permissions are newer/inconsistently documented; if the tenant refuses
app-only, we switch to the delegated (on-behalf-of) flow. This client isolates all Graph calls.
"""
import os, time, threading, requests
from config import MOCK
# planner client: Graph app-only; supports auto-provisioning the plan (see ensure_plan below)

GRAPH = "https://graph.microsoft.com/v1.0"
APP_ID = os.getenv("MICROSOFT_APP_ID", "").strip()
APP_PW = os.getenv("MICROSOFT_APP_PASSWORD", "").strip()
TENANT = os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()
PLAN_ID = os.getenv("PLANNER_PLAN_ID", "").strip()
DEFAULT_BUCKET = os.getenv("PLANNER_DEFAULT_BUCKET_ID", "").strip()
DEFAULT_ASSIGNEE = os.getenv("PLANNER_DEFAULT_ASSIGNEE", "").strip()   # optional email
OWNER_LABEL = os.getenv("PLANNER_OWNER_LABEL", "Team").strip()

# --- auto-provision settings (only used by ensure_plan / create_plan) ---
# The Plan's home. A Planner Plan MUST hang off an M365 Group, so we find-or-create one.
GROUP_NAME = os.getenv("PLANNER_GROUP_NAME", "Messer 1MW").strip()
GROUP_MAILNICK = os.getenv("PLANNER_GROUP_MAILNICK", "messer1mw").strip()
PLAN_TITLE = os.getenv("PLANNER_PLAN_TITLE", "Messer 1MW — Project Schedule").strip()
# A user (email) to set as group owner+member. Recommended: app-only groups have no owner,
# which can make Planner reject plan creation. Set this to your admin/test account.
GROUP_OWNER = os.getenv("PLANNER_GROUP_OWNER", "").strip()

# PLAN_ID can be discovered at runtime by ensure_plan(); keep it mutable at module level.
_active_plan = {"id": PLAN_ID}

# Per-request project routing. Thread-local so concurrent background tasks (Teams messages
# handled in a thread pool) never race each other's plan. None => behave exactly as before.
_tls = threading.local()


def set_active_plan(pid):
    _tls.plan = (str(pid).strip() or None) if pid else None

_MOCK = [{"gid": "p1", "name": "Sample Planner task", "completed": False}]
_tok = {"v": None, "exp": 0}


def _graph_token():
    if _tok["v"] and _tok["exp"] > time.time() + 60:
        return _tok["v"]
    url = "https://login.microsoftonline.com/" + TENANT + "/oauth2/v2.0/token"
    r = requests.post(url, data={"grant_type": "client_credentials", "client_id": APP_ID,
        "client_secret": APP_PW, "scope": "https://graph.microsoft.com/.default"}, timeout=15)
    r.raise_for_status()
    j = r.json()
    _tok["v"] = j["access_token"]; _tok["exp"] = time.time() + int(j.get("expires_in", 3600))
    return _tok["v"]


def _h(extra=None):
    h = {"Authorization": "Bearer " + _graph_token(), "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def get_me():
    return OWNER_LABEL  # app-only has no signed-in user; label used only in the task title


def _plan_id():
    """The plan the bot writes to. Order: per-request override (multi-project routing) >
    explicit env PLANNER_PLAN_ID > whatever ensure_plan() discovered. Auto-provision if empty."""
    ov = getattr(_tls, "plan", None)
    if ov:
        return ov
    if _active_plan["id"]:
        return _active_plan["id"]
    if not MOCK:
        ensure_plan()          # lazy: create the plan the first time a task is logged
    return _active_plan["id"]


def _resolve_user(email):
    try:
        r = requests.get(GRAPH + "/users/" + email + "?$select=id", headers=_h(), timeout=15)
        if r.ok:
            return r.json().get("id")
    except Exception:
        pass
    return None


def create_task(name, notes="", due_on=None, assignee=None, bucket_id=None, start_on=None):
    """Signature matches asana_client.create_task so the bot can call either.
    bucket_id (Planner column), due_on and start_on (YYYY-MM-DD) are Planner extras — the bot
    passes none of them, but the CPM schedule importer does."""
    if MOCK:
        t = {"gid": "p" + str(2000 + len(_MOCK)), "name": name, "completed": False,
             "bucket_id": bucket_id, "start": start_on, "due": due_on}
        _MOCK.append(t); return t
    body = {"planId": _plan_id(), "title": name[:255]}
    bkt = bucket_id or DEFAULT_BUCKET
    if bkt:
        body["bucketId"] = bkt
    if due_on:  # Graph wants an ISO datetime; anchor the date to midnight UTC
        body["dueDateTime"] = str(due_on)[:10] + "T00:00:00Z"
    if start_on:
        body["startDateTime"] = str(start_on)[:10] + "T00:00:00Z"
    email = assignee if (assignee and "@" in assignee) else (DEFAULT_ASSIGNEE or None)
    if email:
        uid = _resolve_user(email)
        if uid:
            body["assignments"] = {uid: {"@odata.type": "#microsoft.graph.plannerAssignment",
                                         "orderHint": " !"}}
    r = requests.post(GRAPH + "/planner/tasks", headers=_h(), json=body, timeout=20)
    r.raise_for_status()
    tid = r.json()["id"]
    if notes:  # description lives on task "details" and needs the current ETag
        d = requests.get(GRAPH + "/planner/tasks/" + tid + "/details", headers=_h(), timeout=15)
        etag = d.json().get("@odata.etag") if d.ok else None
        if etag:
            requests.patch(GRAPH + "/planner/tasks/" + tid + "/details",
                           headers=_h({"If-Match": etag}), json={"description": notes}, timeout=15)
    return {"gid": tid, "name": name}


def set_dates(gid, start_on=None, due_on=None):
    """PATCH start/due on an EXISTING task (CPM schedule sync). Dates = YYYY-MM-DD.
    Needs the current task ETag (If-Match). Returns True on success."""
    if not (start_on or due_on):
        return False
    if MOCK:
        for t in _MOCK:
            if t["gid"] == str(gid):
                if start_on:
                    t["start"] = start_on
                if due_on:
                    t["due"] = due_on
                return True
        return False
    g = requests.get(GRAPH + "/planner/tasks/" + str(gid), headers=_h(), timeout=15)
    if not g.ok:
        return False
    etag = g.json().get("@odata.etag")
    body = {}
    if start_on:
        body["startDateTime"] = str(start_on)[:10] + "T00:00:00Z"
    if due_on:
        body["dueDateTime"] = str(due_on)[:10] + "T00:00:00Z"
    r = requests.patch(GRAPH + "/planner/tasks/" + str(gid),
                       headers=_h({"If-Match": etag}), json=body, timeout=15)
    return r.ok


def complete_task(gid):
    if MOCK:
        for t in _MOCK:
            if t["gid"] == str(gid):
                t["completed"] = True; return t
        return None
    g = requests.get(GRAPH + "/planner/tasks/" + str(gid), headers=_h(), timeout=15)
    if not g.ok:
        return None
    etag = g.json().get("@odata.etag"); name = g.json().get("title")
    requests.patch(GRAPH + "/planner/tasks/" + str(gid), headers=_h({"If-Match": etag}),
                   json={"percentComplete": 100}, timeout=15)
    return {"gid": gid, "name": name, "completed": True}


def add_progress(gid, note, percent=None):
    """Log a chat update AS PROGRESS on an existing (planned) task:
    append the note to the task's description and set percentComplete.
    percent: 0-100 (0=not started, 1-99=in progress, 100=complete). Returns True/False."""
    if MOCK:
        for t in _MOCK:
            if t["gid"] == str(gid):
                t.setdefault("log", []).append(note)
                if percent is not None:
                    t["percent"] = percent
                    t["completed"] = (percent >= 100)
                return True
        return False
    stamp = time.strftime("%Y-%m-%d")
    # 1) append note to the task details description (needs the details ETag)
    d = requests.get(GRAPH + "/planner/tasks/" + str(gid) + "/details", headers=_h(), timeout=15)
    if d.ok:
        etag = d.json().get("@odata.etag")
        old = d.json().get("description") or ""
        new = (old + "\n" if old else "") + "[" + stamp + "] " + note
        if etag:
            requests.patch(GRAPH + "/planner/tasks/" + str(gid) + "/details",
                           headers=_h({"If-Match": etag}), json={"description": new}, timeout=15)
    # 2) set percentComplete on the task itself (needs the task ETag)
    if percent is not None:
        g = requests.get(GRAPH + "/planner/tasks/" + str(gid), headers=_h(), timeout=15)
        if g.ok:
            etag = g.json().get("@odata.etag")
            requests.patch(GRAPH + "/planner/tasks/" + str(gid), headers=_h({"If-Match": etag}),
                           json={"percentComplete": int(percent)}, timeout=15)
    return True


def assign_task(gid, email):
    """Set the assignee on an existing task (idempotent). Returns True if assigned.
    Needs Graph 'User.Read.All' (Application) to resolve the email -> user id; if that
    permission isn't granted yet, _resolve_user returns None and we skip (no crash)."""
    if not email:
        return False
    if MOCK:
        for t in _MOCK:
            if t["gid"] == str(gid):
                t["assignee"] = email
                return True
        return False
    uid = _resolve_user(email)
    if not uid:
        return False
    g = requests.get(GRAPH + "/planner/tasks/" + str(gid), headers=_h(), timeout=15)
    if not g.ok:
        return False
    j = g.json()
    etag = j.get("@odata.etag")
    # REPLACE: null any other current assignee(s), keep/add just this one.
    assigns = {old: None for old in (j.get("assignments") or {}) if old != uid}
    assigns[uid] = {"@odata.type": "#microsoft.graph.plannerAssignment", "orderHint": " !"}
    r = requests.patch(GRAPH + "/planner/tasks/" + str(gid),
                       headers=_h({"If-Match": etag}), json={"assignments": assigns}, timeout=15)
    return r.ok


def list_tasks():
    if MOCK:
        return list(_MOCK)
    r = requests.get(GRAPH + "/planner/plans/" + _plan_id() + "/tasks", headers=_h(), timeout=20)
    r.raise_for_status()
    out = []
    for t in r.json().get("value", []):
        pct = t.get("percentComplete", 0) or 0
        due = t.get("dueDateTime")
        out.append({"gid": t["id"], "name": t.get("title"),
                    "completed": (pct == 100), "percent": pct,
                    "bucket_id": t.get("bucketId"),
                    "due": due[:10] if due else None,
                    "assignees": list((t.get("assignments") or {}).keys())})
    return out


def list_buckets():
    """Return [{id, name}] for the plan's buckets (Planner columns)."""
    if MOCK:
        return []
    r = requests.get(GRAPH + "/planner/plans/" + _plan_id() + "/buckets", headers=_h(), timeout=20)
    r.raise_for_status()
    return [{"id": b["id"], "name": b.get("name")} for b in r.json().get("value", [])]


def create_bucket(name):
    """Create a bucket (column) in the plan and return its id."""
    if MOCK:
        return "b" + str(abs(hash(name)) % 10000)
    body = {"name": name[:255], "planId": _plan_id(), "orderHint": " !"}
    r = requests.post(GRAPH + "/planner/buckets", headers=_h(), json=body, timeout=20)
    r.raise_for_status()
    return r.json()["id"]


def bucket_id_for(name):
    """Return the id of an existing bucket whose name matches (case-insensitive), else None.
    Used by the Teams bot to drop a chat task into the right phase column."""
    if MOCK or not name:
        return None
    want = name.strip().lower()
    for b in list_buckets():
        if (b.get("name") or "").strip().lower() == want:
            return b["id"]
    return None


# ---------------------------------------------------------------------------
# AUTO-PROVISION: create the Plan (and its M365 Group) from code, no clicking.
#
# Why this is needed: a Planner *task* must live in a *Plan*, and a *Plan* must
# hang off an M365 *Group*. Asana already had its project; Planner starts empty.
# ensure_plan() does the one-time setup: find-or-create the group, then
# find-or-create the plan inside it, and remembers the plan id for this process.
#
# Graph permissions required (Application, admin-consented on the bot's app):
#   - Tasks.ReadWrite.All   (create/read Planner tasks & plans)   [already granted]
#   - Group.ReadWrite.All   (create the group + set its owner)     [add for auto-create]
#   - Group.Read.All        (find an existing group by name)       [add for find-only]
# If you'd rather not grant Group.ReadWrite.All, create the group once by hand
# (or reuse a Team) and pass its id via PLANNER_GROUP_ID — then only the plan is
# auto-created, which needs just Tasks.ReadWrite.All.
# ---------------------------------------------------------------------------
GROUP_ID_ENV = os.getenv("PLANNER_GROUP_ID", "").strip()


def find_group(name):
    """Return the id of an existing M365 group matching displayName, or None."""
    q = GRAPH + "/groups?$filter=displayName eq '" + name.replace("'", "''") + "'&$select=id,displayName"
    r = requests.get(q, headers=_h(), timeout=20)
    if r.ok:
        vals = r.json().get("value", [])
        if vals:
            return vals[0]["id"]
    return None


def create_group(name, mail_nickname, owner_email=None):
    """Create a Microsoft 365 (Unified) group to host the plan. Optionally add a user as
    owner+member so Planner accepts plan creation. Returns the new group id."""
    body = {
        "displayName": name,
        "mailEnabled": True,
        "mailNickname": mail_nickname,
        "securityEnabled": False,
        "groupTypes": ["Unified"],
    }
    if owner_email:
        uid = _resolve_user(owner_email)
        if uid:
            ref = GRAPH + "/users/" + uid
            body["owners@odata.bind"] = [ref]
            body["members@odata.bind"] = [ref]
    r = requests.post(GRAPH + "/groups", headers=_h(), json=body, timeout=30)
    r.raise_for_status()
    gid = r.json()["id"]
    # Planner backing store can lag a few seconds after group creation.
    time.sleep(8)
    return gid


def find_plan_in_group(group_id, title):
    """Return the id of a plan with this title already in the group, or None."""
    r = requests.get(GRAPH + "/groups/" + group_id + "/planner/plans?$select=id,title",
                     headers=_h(), timeout=20)
    if r.ok:
        for p in r.json().get("value", []):
            if p.get("title") == title:
                return p["id"]
    return None


def create_plan(title, group_id):
    """Create a Planner plan owned by the given group. Tries the modern 'container' shape,
    then falls back to the legacy 'owner' shape for older tenants. Returns the plan id."""
    # modern shape (v1.0)
    body = {"container": {"url": GRAPH + "/groups/" + group_id}, "title": title}
    r = requests.post(GRAPH + "/planner/plans", headers=_h(), json=body, timeout=30)
    if r.status_code in (400, 403):
        # legacy shape (older tenants)
        r = requests.post(GRAPH + "/planner/plans", headers=_h(),
                          json={"owner": group_id, "title": title}, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def ensure_plan():
    """Idempotent one-time setup. Returns the plan id and caches it on the module.
    Order: explicit PLANNER_PLAN_ID > find/create group > find/create plan."""
    if _active_plan["id"]:
        return _active_plan["id"]

    gid = GROUP_ID_ENV or find_group(GROUP_NAME)
    if not gid:
        print("• group '" + GROUP_NAME + "' not found — creating it")
        gid = create_group(GROUP_NAME, GROUP_MAILNICK, GROUP_OWNER or None)
    print("• group id:", gid)

    pid = find_plan_in_group(gid, PLAN_TITLE)
    if pid:
        print("• plan already exists:", pid)
    else:
        print("• creating plan '" + PLAN_TITLE + "'")
        pid = create_plan(PLAN_TITLE, gid)
        print("• plan created:", pid)

    _active_plan["id"] = pid
    return pid


if __name__ == "__main__":
    # Engineer CLI:  python planner_client.py ensure-plan
    # Prints the plan id to paste into Railway as PLANNER_PLAN_ID (optional — the bot
    # will also auto-provision lazily on the first task if the id is left unset).
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ensure-plan"
    if cmd == "ensure-plan":
        print("PLANNER_PLAN_ID=" + ensure_plan())
    elif cmd == "list":
        for t in list_tasks():
            print(t)
    else:
        print("usage: python planner_client.py [ensure-plan|list]")
