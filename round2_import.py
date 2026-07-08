"""Load the BKI Round 2 Sprint tasks into the Hydrogen Mobility Planner as a dedicated
bucket ("R2 - Round 2 Sprint"), assigned per person with due dates. Idempotent - re-run
never duplicates. Does NOT touch the WS1-WS9 schedule.

Run in the Railway Console against the Hydrogen Mobility plan:

    PLANNER_PLAN_ID=w2f8-ZSUpUWdD7jXKAzf0MkAAgr7 python round2_import.py
"""
import os, json
import planner_client as pc

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "round2_sprint.json")


def main():
    rows = json.load(open(DATA, encoding="utf-8"))
    section = rows[0]["section"]
    print("Round 2 Sprint: %d tasks -> bucket '%s'" % (len(rows), section))
    pid = pc.ensure_plan()
    print("Plan:", pid)

    buckets = {b["name"]: b["id"] for b in pc.list_buckets()}
    if section not in buckets:
        buckets[section] = pc.create_bucket(section)
        print("+ bucket:", section)
    bid = buckets[section]

    existing = {}
    for t in pc.list_tasks():
        existing[(t.get("bucket_id"), t["name"])] = t["gid"]

    created = found = assigned = 0
    for r in rows:
        owner, due, key = r.get("assignee"), r.get("due"), (bid, r["name"])
        if key in existing:
            gid = existing[key]; found += 1
            pc.set_dates(gid, due_on=due)
        else:
            t = pc.create_task(r["name"], notes=r.get("notes", ""), due_on=due,
                               assignee=owner, bucket_id=bid)
            gid = t["gid"]; existing[key] = gid; created += 1
            print("  + " + r["name"])
        if owner and pc.assign_task(gid, owner):
            assigned += 1

    print("Done. created=%d  existing=%d  assigned=%d" % (created, found, assigned))


if __name__ == "__main__":
    main()
