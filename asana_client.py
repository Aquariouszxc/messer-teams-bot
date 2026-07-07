"""Asana REST client. Asana is the source of truth. Falls back to in-memory MOCK data."""
import requests
from config import ASANA_TOKEN, ASANA_PROJECT_GID, MOCK

BASE = "https://app.asana.com/api/1.0"
def _h():
    return {"Authorization": f"Bearer {ASANA_TOKEN}", "Content-Type": "application/json"}

# ---- MOCK store (used when no token) ----
_MOCK = [
    {"gid": "1001", "name": "Finalise FCM360 pricing & PO readiness", "completed": False,
     "assignee": {"name": "Powertrain"}, "due_on": "2026-08-01", "notes": ""},
    {"gid": "1002", "name": "Install & calibrate H2 flow meter", "completed": False,
     "assignee": {"name": "Mechatronics"}, "due_on": "2026-08-05", "notes": ""},
    {"gid": "1003", "name": "MRV weighted-EF certificate demo", "completed": True,
     "assignee": {"name": "Yelin (IT/MRV)"}, "due_on": "2026-07-01", "notes": ""},
]

def list_tasks():
    if MOCK:
        return list(_MOCK)
    r = requests.get(f"{BASE}/projects/{ASANA_PROJECT_GID}/tasks",
                     headers=_h(), params={"opt_fields": "name,completed,assignee.name,due_on,notes"},
                     timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])

def create_task(name, notes="", due_on=None, assignee=None):
    """assignee: an Asana user gid or the string 'me' (token owner)."""
    if MOCK:
        t = {"gid": str(2000 + len(_MOCK)), "name": name, "completed": False,
             "assignee": {"name": assignee or "me"} if assignee else None,
             "due_on": due_on, "notes": notes}
        _MOCK.append(t); return t
    data = {"name": name, "notes": notes, "projects": [ASANA_PROJECT_GID]}
    if due_on: data["due_on"] = due_on
    if assignee: data["assignee"] = assignee
    r = requests.post(f"{BASE}/tasks", headers=_h(), json={"data": data}, timeout=15)
    r.raise_for_status(); return r.json().get("data", {})

def add_progress(gid, note, percent=None):
    """Log a chat update AS PROGRESS on an existing (planned) task: add a comment (story)
    and, if percent>=100, mark the task complete. (Asana has no native % field, so the
    percent is written into the comment text.) Returns True/False."""
    text = note if percent is None else (note + "  [" + str(int(percent)) + "%]")
    if MOCK:
        for t in _MOCK:
            if t["gid"] == str(gid):
                t.setdefault("log", []).append(text)
                if percent is not None and percent >= 100:
                    t["completed"] = True
                return True
        return False
    try:
        requests.post(f"{BASE}/tasks/{gid}/stories", headers=_h(),
                      json={"data": {"text": text}}, timeout=15)
        if percent is not None and percent >= 100:
            requests.put(f"{BASE}/tasks/{gid}", headers=_h(),
                         json={"data": {"completed": True}}, timeout=15)
        return True
    except Exception:
        return False


def complete_task(gid):
    if MOCK:
        for t in _MOCK:
            if t["gid"] == str(gid): t["completed"] = True; return t
        return None
    r = requests.put(f"{BASE}/tasks/{gid}", headers=_h(), json={"data": {"completed": True}}, timeout=15)
    r.raise_for_status(); return r.json().get("data", {})

def get_task(gid):
    if MOCK:
        return next((t for t in _MOCK if t["gid"] == str(gid)), None)
    r = requests.get(f"{BASE}/tasks/{gid}", headers=_h(),
                     params={"opt_fields": "name,completed,assignee.name,due_on,notes"}, timeout=15)
    r.raise_for_status(); return r.json().get("data", {})


_ME_CACHE = {}
def get_me():
    """Return the token owner's display name (cached). Mock returns a placeholder."""
    if _ME_CACHE.get("name"):
        return _ME_CACHE["name"]
    if MOCK:
        _ME_CACHE["name"] = "Yelin Htet (mock)"; return _ME_CACHE["name"]
    try:
        r = requests.get(f"{BASE}/users/me", headers=_h(), params={"opt_fields": "name"}, timeout=15)
        r.raise_for_status()
        _ME_CACHE["name"] = r.json().get("data", {}).get("name", "")
    except Exception:
        _ME_CACHE["name"] = ""
    return _ME_CACHE["name"]
