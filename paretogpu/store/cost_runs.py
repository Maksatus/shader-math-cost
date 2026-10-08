"""Every run of `cost` on a snapshot, kept: <out>/costs/<time>.json with a small <time>.meta.json for listing,
next to frame_cost.json (the latest run). A snapshot priced again after a shader change is compared with its earlier
runs (core/compare.py).
"""
import json
import os
import shutil
import time

COSTS = "costs"


def archive(c, out):
    """Keep this run of `cost` as <out>/costs/<time>.json (+ .meta.json); returns the run id (<time>)."""
    d = os.path.join(out, COSTS)
    os.makedirs(d, exist_ok=True)
    rid = time.strftime("%Y%m%d_%H%M%S", time.localtime(c.get("computed_at") or time.time()))
    while os.path.exists(os.path.join(d, rid + ".json")):  # two runs in one second
        rid += "b"
    shutil.copyfile(os.path.join(out, "frame_cost.json"), os.path.join(d, rid + ".json"))
    with open(os.path.join(d, rid + ".meta.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta(c, rid), f, indent=1, ensure_ascii=False)
    return rid


def meta(c, rid=None):
    return {"id": rid, "computed_at": c.get("computed_at"), "frame_dir": c.get("frame_dir"),
            "project": (c.get("frame") or {}).get("project"), "api": c.get("api"), "malioc": c.get("malioc"),
            "cores": [x["name"] for x in c.get("cores") or []], "main_core": c.get("main_core"),
            "totals": {k: v.get("total") for k, v in (c.get("totals") or {}).items()},
            "loops_n": (c.get("loops") or {}).get("n"), "checked": c.get("variants_checked"),
            "coverage": c.get("coverage")}


def runs(folder):
    """Metas of the runs kept in <folder>/costs, oldest first."""
    d = os.path.join(folder, COSTS)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".meta.json"):
                try:
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        out.append(json.load(f))
                except (OSError, ValueError):
                    pass
    return sorted(out, key=lambda m: m.get("computed_at") or 0)


def run_path(folder, rid):
    return os.path.join(folder, COSTS, rid + ".json")


def load(path):
    """A cost run: frame_cost.json, a costs/<time>.json, or a snapshot folder (its latest run)."""
    if os.path.isdir(path):
        path = os.path.join(path, "frame_cost.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
