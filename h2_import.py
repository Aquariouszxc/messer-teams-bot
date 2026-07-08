"""One-command importer for the Indefol Hydrogen Mobility project.

Reads the CPM-computed schedule (computed_h2.json = single source of truth: start + due
from the Critical Path Method) and syncs it into the Hydrogen Mobility Planner plan:
WS1-WS9 buckets, WBS tasks, per-person assignment, and START + DUE dates.
Idempotent - re-run never duplicates; existing tasks just get their dates refreshed.

Regenerate the schedule first if the model changed:
    python cpm.py schedule_model_h2.json --out computed_h2.json

Then run in the Railway Console, pointing PLANNER_PLAN_ID at the Hydrogen Mobility plan
(overrides for THIS process only; the running web service is unaffected):

    PLANNER_PLAN_ID=w2f8-ZSUpUWdD7jXKAzf0MkAAgr7 python h2_import.py

Needs the granted Graph perms (Tasks.ReadWrite.All + User.Read.All for assignment).
"""
import os, json
import planner_client as pc

HERE = os.path.dirname(os.path.abspath(__file__))
COMPUTED = os.path.join(HERE, "computed_h2.json")
SCHEDULE = os.path.join(HERE, "h2_schedule.json")   # for task notes (not in computed file)


def main():
    comp = json.load(open(COMPUTED, encoding="utf-8"))
    rows = comp["tasks"]
    notes_by_name = {}
    try:
        for r in json.load(open(SCHEDULE, encoding="utf-8")):
            notes_by_name[r["name"]] = r.get("notes", "")
    except Exception:
        pass

    sections = []
    for r in rows:
        if r["section"] not in sections:
            sections.append(r["section"])
    print("Indefol Hydrogen Mobility (CPM schedule): %d tasks / %d buckets  |  finish %s  buffer %sd"
          % (len(rows), len(sections), comp.get("finish"), comp.get("buffer_days")))
    pid = pc.ensure_plan()
    print("Plan:", pid)

    buckets = {b["name"]: b["id"] for b in pc.list_buckets()}
    for s in sections:
        if s not in buckets:
            buckets[s] = pc.create_bucket(s)
            print("+ bucket:", s)

    existing = {}
    for t in pc.list_tasks():
        existing[(t.get("bucket_id"), t["name"])] = t["gid"]

    created = found = dated = assigned = 0
    for r in rows:
        bid = buckets[r["section"]]
        owner = r.get("assignee")
        start, due = r.get("start"), r.get("due")
        key = (bid, r["name"])
        if key in existing:
            gid = existing[key]
            found += 1
            if pc.set_dates(gid, start_on=start, due_on=due):   # refresh CPM dates
                dated += 1
        else:
            t = pc.create_task(r["name"], notes=notes_by_name.get(r["name"], ""),
                               due_on=due, start_on=start, assignee=owner, bucket_id=bid)
            gid = t["gid"]
            existing[key] = gid
            created += 1
            dated += 1
            print("  + [%s] %s  (%s -> %s)" % (r["section"], r["name"], start, due))
        if owner and pc.assign_task(gid, owner):
            assigned += 1

    print("Done. created=%d  existing=%d  dated=%d  assigned=%d  buckets=%d"
          % (created, found, dated, assigned, len(buckets)))


if __name__ == "__main__":
    main()
