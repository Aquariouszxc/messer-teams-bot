"""Microsoft Planner via Graph (app-only) — mirrors asana_client's interface so the bot
can write to Planner instead of Asana by setting DEST=planner.

Uses the SAME app credentials as the bot (MICROSOFT_APP_ID/PASSWORD/TENANT), requesting a
Graph token (scope https://graph.microsoft.com/.default). Requires the app to have the Graph
application permission Tasks.ReadWrite.All with admin consent.

NOTE: Planner app-only permissions are newer/inconsistently documented; if the tenant refuses
app-only, we switch to the delegated (on-behalf-of) flow. This client isolates all Graph calls.
"""
import os, time, requests
from config import MOCK

GRAPH = "https://graph.microsoft.com/v1.0"
APP_ID = os.getenv("MICROSOFT_APP_ID", "").strip()
APP_PW = os.getenv("MICROSOFT_APP_PASSWORD", "").strip()
TENANT = os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()
PLAN_ID = os.getenv("PLANNER_PLAN_ID", "").strip()
DEFAULT_BUCKET = os.getenv("PLANNER_DEFAULT_BUCKET_ID", "").strip()
DEFAULT_ASSIGNEE = os.getenv("PLANNER_DEFAULT_ASSIGNEE", "").strip()   # optional email
OWNER_LABEL = os.getenv("PLANNER_OWNER_LABEL", "Team").strip()

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


def _resolve_user(email):
    try:
        r = requests.get(GRAPH + "/users/" + email + "?$select=id", headers=_h(), timeout=15)
        if r.ok:
            return r.json().get("id")
    except Exception:
        pass
    return None


def create_task(name, notes="", due_on=None, assignee=None):
    """Signature matches asana_client.create_task so the bot can call either."""
    if MOCK:
        t = {"gid": "p" + str(2000 + len(_MOCK)), "name": name, "completed": False}
        _MOCK.append(t); return t
    body = {"planId": PLAN_ID, "title": name[:255]}
    if DEFAULT_BUCKET:
        body["bucketId"] = DEFAULT_BUCKET
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


def list_tasks():
    if MOCK:
        return list(_MOCK)
    r = requests.get(GRAPH + "/planner/plans/" + PLAN_ID + "/tasks", headers=_h(), timeout=20)
    r.raise_for_status()
    out = []
    for t in r.json().get("value", []):
        out.append({"gid": t["id"], "name": t.get("title"),
                    "completed": (t.get("percentComplete") == 100)})
    return out
