"""Where the results live: paretogpu/out and paretogpu/corpus/real. Neither ever reaches git: they hold project
shaders and assets.

  out/frame_<project>_<time>[_<suffix>]/   a snapshot (frame_events.json) and its costs (store/cost_runs.py)
  out/variants_<project>/                  the compiled variants of a project, shared by its frames and materials
  out/_matcompare/<time>/, _matshader/<time>/   analyses of materials (no snapshot needed)
  out/_ui/                                 the UI's settings, run history and logs
  corpus/real/<project>/                   shaders exported with `export`
"""
import json
import os
import re
import time

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PKG, "out")
REAL = os.path.join(PKG, "corpus", "real")
UI = os.path.join(OUT, "_ui")
RESULTS = {"matcompare": "_matcompare", "matshader": "_matshader"}
SNAPSHOT_FILE = "frame_events.json"


def time_id():
    return time.strftime("%Y%m%d_%H%M%S")


def frame_dir_name(project, stamp, suffix=None):
    """frame_<project>_<time>[_<suffix>]: the suffix keeps latin letters, digits, '-' and '_' only."""
    name = f"frame_{os.path.basename(os.path.abspath(project))}_{stamp}"
    suffix = re.sub(r"[^\w-]+", "_", (suffix or "").strip(), flags=re.ASCII).strip("_")
    return f"{name}_{suffix}" if suffix else name


def new_snapshot_dir(project, suffix=None):
    return os.path.join(OUT, frame_dir_name(project, time_id(), suffix))


def variants_dir(project):
    """One variants folder per project: a variant is compiled once and priced in every frame of the project."""
    return os.path.join(OUT, f"variants_{os.path.basename(os.path.abspath(project))}")


def export_dir(project):
    return os.path.join(REAL, os.path.basename(os.path.abspath(project)))


def results_dir(kind):
    return os.path.join(OUT, RESULTS[kind])


def new_result_dir(kind):
    return os.path.join(results_dir(kind), time_id())


def is_snapshot(path):
    return os.path.exists(os.path.join(path, SNAPSHOT_FILE))


def snapshot_dirs():
    """[(name, path)] of the snapshots in out/."""
    if not os.path.isdir(OUT):
        return []
    return [(name, os.path.join(OUT, name)) for name in os.listdir(OUT) if is_snapshot(os.path.join(OUT, name))]


def snapshot_dir(name):
    """A snapshot folder directly in out/ by its name, or None (nothing else is deleted or opened by name)."""
    if not name or os.path.basename(name) != name or name.startswith((".", "_")):
        return None
    path = os.path.join(OUT, name)
    return path if is_snapshot(path) else None


def variants_dirs():
    """The variants folders in out/."""
    if not os.path.isdir(OUT):
        return []
    return [os.path.join(OUT, n) for n in sorted(os.listdir(OUT))
            if n.startswith("variants") and os.path.isdir(os.path.join(OUT, n))]


def export_dirs():
    return [os.path.join(REAL, n) for n in sorted(os.listdir(REAL))] if os.path.isdir(REAL) else []


def snapshots_of(project, folders):
    """Events of every frame_events.json under `folders` (one level of subfolders) taken in `project`."""
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
