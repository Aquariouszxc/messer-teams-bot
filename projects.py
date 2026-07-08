"""Multi-project routing for the shared bot.

Two projects share one Railway service / one Teams bot:
  - messer : Messer 1MW (default; plan from env PLANNER_PLAN_ID)
  - h2     : Indefol Hydrogen Mobility (plan w2f8-...)

Each user has an ACTIVE project (persisted on the volume). A person switches once
with `/project h2` or `/project messer`; it sticks. Messer is the default, so the
existing Messer behaviour is unchanged for anyone who never switches.
"""
import os, json, threading

_LOCK = threading.Lock()
ACTIVE_FILE = os.getenv("PROJECT_ACTIVE_FILE") or (
    "/data/active_project.json" if os.path.isdir("/data") else "active_project.json")

PROJECTS = {
    "messer": {"name": "Messer 1MW", "plan_id": os.getenv("PLANNER_PLAN_ID", "").strip()},
    "h2": {"name": "Indefol Hydrogen Mobility",
           "plan_id": os.getenv("H2_PLAN_ID", "w2f8-ZSUpUWdD7jXKAzf0MkAAgr7").strip()},
}
DEFAULT_PROJECT = os.getenv("DEFAULT_PROJECT_KEY", "messer").strip().lower()

# email/name aliases -> project key (for the switch command)
ALIASES = {
    "h2": "h2", "hydrogen": "h2", "mobility": "h2", "indefol": "h2", "hym": "h2",
    "messer": "messer", "msr": "messer", "1mw": "messer",
}

# Optional per-user default (Azure AD object id -> project). Users can still override
# with /project; this just sets their starting project. admin@johnlyser has no Messer
# tasks, only Hydrogen Mobility.
USER_HOME = {
    "37e09496-a281-40ea-b339-410d76835e30": "h2",   # admin@johnlyser.com (Yelin) -> H2
}


def _load():
    try:
        with _LOCK:
            if os.path.exists(ACTIVE_FILE):
                return json.load(open(ACTIVE_FILE, encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save(d):
    try:
        with _LOCK:
            json.dump(d, open(ACTIVE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def get_project_key(oid):
    """Which project this user is currently on."""
    if oid:
        a = _load()
        if oid in a and a[oid] in PROJECTS:
            return a[oid]
        if oid in USER_HOME and USER_HOME[oid] in PROJECTS:
            return USER_HOME[oid]
    return DEFAULT_PROJECT if DEFAULT_PROJECT in PROJECTS else "messer"


def set_project(oid, key):
    key = (key or "").lower()
    if key not in PROJECTS or not oid:
        return False
    a = _load()
    a[oid] = key
    _save(a)
    return True


def resolve_alias(word):
    """Map a /project argument (h2 / hydrogen / messer ...) to a project key, or None."""
    return ALIASES.get((word or "").strip().lower())


def plan_id_for(key):
    return PROJECTS.get(key, {}).get("plan_id") or None


def name_for(key):
    return PROJECTS.get(key, {}).get("name") or key
