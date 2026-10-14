"""paretogpu/out (never in git): frame_<project>_<time>[_<suffix>]/ snapshots, variants_<project>/ (shared by frames
and materials), _matcompare/<time>/, _matshader/<time>/, _ui/ (settings, history, logs)."""
import json
import os
import re
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PKG, "out")
UI = os.path.join(OUT, "_ui")
RESULTS = {"matcompare": "_matcompare", "matshader": "_matshader"}
SNAPSHOT_FILE = "frame_events.json"
COST_FILE = "frame_cost.json"
COST_CSV = "frame_cost.csv"
REPORT_PAGE = "frame_report.html"
HOTSPOTS_PAGE = "hotspots.html"


def time_id():
    return time.strftime("%Y%m%d_%H%M%S")


def frame_dir_name(project, stamp, suffix=None):
    name = f"frame_{os.path.basename(os.path.abspath(project))}_{stamp}"
    suffix = re.sub(r"[^\w-]+", "_", (suffix or "").strip(), flags=re.ASCII).strip("_")
    return f"{name}_{suffix}" if suffix else name


def new_snapshot_dir(project, suffix=None):
    return os.path.join(OUT, frame_dir_name(project, time_id(), suffix))


def variants_dir(project):
    return os.path.join(OUT, f"variants_{os.path.basename(os.path.abspath(project))}")


def results_dir(kind):
    return os.path.join(OUT, RESULTS[kind])


def new_result_dir(kind):
    return os.path.join(results_dir(kind), time_id())


def snapshot_project(path):
    try:
        with open(os.path.join(path or "", SNAPSHOT_FILE), encoding="utf-8") as f:
            return json.load(f).get("project")
    except (OSError, ValueError):
        return None


def is_snapshot(path):
    return os.path.exists(os.path.join(path, SNAPSHOT_FILE))


def snapshot_dirs():
    if not os.path.isdir(OUT):
        return []
    return [(name, os.path.join(OUT, name)) for name in os.listdir(OUT) if is_snapshot(os.path.join(OUT, name))]


def snapshot_dir(name):
    """Only a snapshot directly in out/, else None: nothing else may be deleted or opened by name."""
    if not name or os.path.basename(name) != name or name.startswith((".", "_")):
        return None
    path = os.path.join(OUT, name)
    return path if is_snapshot(path) else None


def variants_dirs():
    if not os.path.isdir(OUT):
        return []
    return [os.path.join(OUT, n) for n in sorted(os.listdir(OUT))
            if n.startswith("variants") and os.path.isdir(os.path.join(OUT, n))]


def snapshots_of(project, folders):
    out, seen = [], set()
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for d in [folder] + [os.path.join(folder, x) for x in sorted(os.listdir(folder))]:
            p = os.path.join(d, SNAPSHOT_FILE)
            if p in seen or not os.path.exists(p):
                continue
            seen.add(p)
            with open(p, encoding="utf-8") as f:
                fr = json.load(f)
            if os.path.normcase(os.path.abspath(fr.get("project") or "")) == os.path.normcase(os.path.abspath(project)):
                out.append(fr["events"])
    return out
