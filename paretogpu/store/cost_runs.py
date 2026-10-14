"""Every `cost` run kept as <snapshot>/costs/<time>.json + <time>.meta.json; frame_cost.json is the latest run."""
import json
import os
import shutil
import time

from paretogpu.core import frame_cost
from paretogpu.store import workspace

COSTS = "costs"
META = ("computed_at", "frame_dir", "project", "api", "malioc", "cores", "main_core", "totals", "loops_n", "checked",
        "coverage")


def archive(c, out):
    """-> run id (<time>)."""
    d = os.path.join(out, COSTS)
    os.makedirs(d, exist_ok=True)
    rid = time.strftime("%Y%m%d_%H%M%S", time.localtime(c.get("computed_at") or time.time()))
    while os.path.exists(os.path.join(d, rid + ".json")):  # two runs in one second
        rid += "b"
    shutil.copyfile(os.path.join(out, workspace.COST_FILE), os.path.join(d, rid + ".json"))
    with open(os.path.join(d, rid + ".meta.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta(c, rid), f, indent=1, ensure_ascii=False)
    return rid


def meta(c, rid=None):
    s = frame_cost.summary(c)
    return {"id": rid, **{k: s[k] for k in META}}


def runs(folder):
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
    if os.path.isdir(path):
        path = os.path.join(path, workspace.COST_FILE)
    with open(path, encoding="utf-8") as f:
        return json.load(f)
