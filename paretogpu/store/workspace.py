"""Where the results live: paretogpu/out (snapshots, the variants folders, analyses, the UI's state) and
paretogpu/corpus/real (exported shaders). Neither ever reaches git: they hold project shaders and assets.
"""
import json
import os
import re
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PKG, "out")
REAL = os.path.join(PKG, "corpus", "real")


def time_id():
    return time.strftime("%Y%m%d_%H%M%S")


def frame_dir_name(project, stamp, suffix=None):
    """frame_<project>_<time>[_<suffix>]: the suffix keeps latin letters, digits, '-' and '_' only."""
    name = f"frame_{os.path.basename(os.path.abspath(project))}_{stamp}"
    suffix = re.sub(r"[^\w-]+", "_", (suffix or "").strip(), flags=re.ASCII).strip("_")
    return f"{name}_{suffix}" if suffix else name


def snapshots_of(project, folders):
    """Events of every frame_events.json under `folders` (one level of subfolders) taken in `project`."""
    out, seen = [], set()
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for d in [folder] + [os.path.join(folder, x) for x in sorted(os.listdir(folder))]:
            p = os.path.join(d, "frame_events.json")
            if p in seen or not os.path.exists(p):
                continue
            seen.add(p)
            with open(p, encoding="utf-8") as f:
                fr = json.load(f)
            if os.path.normcase(os.path.abspath(fr.get("project") or "")) == os.path.normcase(os.path.abspath(project)):
                out.append(fr["events"])
    return out
