"""CPM scheduler = single source of truth for task dates.
Reads a schedule model (durations + predecessors), runs the Critical Path Method
(forward pass -> ES/EF, backward pass -> LS/LF, slack), maps to calendar dates from
the project anchor, and writes computed start/due back out for the Planner importer.

Usage:
  python cpm.py schedule_model_h2.json            # print schedule + critical path
  python cpm.py schedule_model_h2.json --out computed_h2.json   # also write dates

Dates are COMPUTED here. Never hand-edit start/due in Planner or the model — edit a
task's `dur` or `preds`, rerun, and every date updates consistently.
"""
import json, sys, datetime


def load(path):
    return json.load(open(path, encoding="utf-8"))


def cpm(model):
    tasks = {t["id"]: t for t in model["tasks"]}
    for t in tasks.values():
        for p in t["preds"]:
            if p not in tasks:
                raise SystemExit(f"Task {t['id']} has unknown predecessor {p}")

    ES, EF = {}, {}

    def ef(t):
        if t in EF:
            return EF[t]
        preds = tasks[t]["preds"]
        ES[t] = 0 if not preds else max(ef(p) for p in preds)
        EF[t] = ES[t] + tasks[t]["dur"]
        return EF[t]

    for t in tasks:
        ef(t)
    proj_end = max(EF.values())

    succs = {t: [] for t in tasks}
    for t in tasks:
        for p in tasks[t]["preds"]:
            succs[p].append(t)

    LF, LS = {}, {}

    def ls(t):
        if t in LS:
            return LS[t]
        s = succs[t]
        LF[t] = proj_end if not s else min(ls(x) for x in s)
        LS[t] = LF[t] - tasks[t]["dur"]
        return LS[t]

    for t in tasks:
        ls(t)

    anchor = datetime.date.fromisoformat(model["anchor_start"])
    rows = []
    for t in tasks:
        slack = LS[t] - ES[t]
        rows.append({
            "id": t,
            "name": tasks[t]["name"],
            "section": tasks[t].get("section", ""),
            "assignee": tasks[t].get("assignee", ""),
            "dur": tasks[t]["dur"],
            "preds": tasks[t]["preds"],
            "ES": ES[t], "EF": EF[t], "LS": LS[t], "LF": LF[t],
            "slack": slack, "critical": slack == 0,
            "start": (anchor + datetime.timedelta(days=ES[t])).isoformat(),
            "due": (anchor + datetime.timedelta(days=EF[t])).isoformat(),
        })
    rows.sort(key=lambda r: (r["ES"], r["EF"]))
    return rows, proj_end, anchor


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python cpm.py <model.json> [--out computed.json]")
    model = load(sys.argv[1])
    rows, proj_end, anchor = cpm(model)
    finish = anchor + datetime.timedelta(days=proj_end)
    launch = datetime.date.fromisoformat(model["launch"])
    buffer = (launch - finish).days

    print(f"Project: {model['project']}")
    print(f"Anchor start: {anchor}   Computed finish: {finish}   "
          f"Launch: {launch}   Buffer: {buffer} days\n")
    print(f"{'id':4} {'start':11} {'due':11} {'dur':>3} {'slk':>3} {'C':1}  task")
    for r in rows:
        print(f"{r['id']:4} {r['start']:11} {r['due']:11} {r['dur']:>3} "
              f"{r['slack']:>3} {'*' if r['critical'] else ' '}  {r['name'][:46]}")

    crit = [r['id'] for r in rows if r['critical']]
    print("\nCritical path (slack 0): " + " -> ".join(crit))

    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        payload = {
            "project": model["project"],
            "anchor_start": anchor.isoformat(),
            "finish": finish.isoformat(),
            "launch": launch.isoformat(),
            "buffer_days": buffer,
            "tasks": [{"id": r["id"], "section": r["section"], "name": r["name"],
                       "assignee": r["assignee"], "start": r["start"], "due": r["due"],
                       "slack": r["slack"], "critical": r["critical"]} for r in rows],
        }
        json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\nWrote computed schedule ({len(rows)} tasks, buffer {buffer}d) -> {out}")


if __name__ == "__main__":
    main()
