"""One-command importer for the Indefol Hydrogen Mobility project.

Populates the Hydrogen Mobility Planner plan with WS1-WS9 buckets and the WBS tasks
(pre-assigned per person, gate-aligned due dates). Idempotent - re-run never duplicates.

Run in the Railway Console, pointing PLANNER_PLAN_ID at the Hydrogen Mobility plan
(this overrides for THIS process only; the running web service is unaffected):

    PLANNER_PLAN_ID=w2f8-ZSUpUWdD7jXKAzf0MkAAgr7 python h2_import.py

Needs the already-granted Graph perms (Tasks.ReadWrite.All + User.Read.All for assignment).
"""
import os, json
import planner_client as pc

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "h2_schedule.json")


def main():
    rows = json.load(open(DATA, encoding="utf-8"))
    sections = []
    for r in rows:
        if r["section"] not in sections:
            sections.append(r["section"])
    print("Indefol Hydrogen Mobility WBS: %d tasks / %d buckets" % (len(rows), len(sections)))
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

    created = found = assigned = 0
    for r in rows:
        bid = buckets[r["section"]]
        owner = r.get("assignee")
        key = (bid, r["name"])
        if key in existing:
            gid = existing[key]
            found += 1
        else:
            t = pc.create_task(r["name"], notes=r.get("notes", ""), due_on=r.get("due") or None,
                               assignee=owner, bucket_id=bid)
            gid = t["gid"]
            existing[key] = gid
            created += 1
            print("  + [%s] %s" % (r["section"], r["name"]))
        if owner and pc.assign_task(gid, owner):
            assigned += 1

    print("Done. created=%d  existing=%d  assigned=%d  buckets=%d"
          % (created, found, assigned, len(buckets)))


if __name__ == "__main__":
    main()
