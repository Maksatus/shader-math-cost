"""What the UI can run: presets (one button = a chain of steps), the commands' forms, their command lines.

A step is `python -m paretogpu <cmd>` (features/: its arguments, phases, the page it writes, the folders the UI fills
in) or a bench script. plan(preset, values) -> the steps with their values.
"""
import os
import sys

from paretogpu.features import BY_NAME, COMMANDS
from paretogpu.store import workspace

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# a preset is what one button runs: its steps; `hide`: arguments the server fills in (the chain's own folders).
# export, measure and report stay in the console: the UI is for checking a frame without knowing the CLI
PRESETS = [
    {"id": "frame_cost", "steps": [{"cmd": "frame", "hide": ["out"]}, {"cmd": "cost", "hide": ["frame", "out", "project"]}]},
    {"id": "cost", "steps": [{"cmd": "cost"}]},
    {"id": "site", "steps": [{"cmd": "bench_run"}, {"cmd": "bench_site"}]},
    {"id": "matcompare", "steps": [{"cmd": "matcompare"}]},
    {"id": "matshader", "steps": [{"cmd": "matshader"}]},
    {"id": "hotspots", "steps": [{"cmd": "hotspots"}]},
]
BENCH = {  # bench scripts are not paretogpu commands: their arguments by hand
    "bench_run": {"help": "measure every function on every Mali GPU (bench/run.py) -> docs/mali_math_cost.csv",
                  "args": [{"dest": "gpus", "flag": "--gpus", "kind": "value", "default": "",
                            "help": "comma separated GPUs (default: all)"},
                           {"dest": "jobs", "flag": "--jobs", "kind": "value", "default": None,
                            "help": "parallel malioc runs (default: CPU count)"}]},
    "bench_site": {"help": "docs/data.js and docs/summary_*.csv from docs/mali_math_cost.csv (bench/build_site.py)",
                   "args": []},
}
BENCH_PHASES = {"bench_run": ["bench_compile"]}
BENCH_SCRIPTS = {"bench_run": ("bench", "run.py"), "bench_site": ("bench", "build_site.py")}


def schema():
    """{command: {"help", "args": [...]}} of the commands (features/), plus the bench scripts."""
    out = {c.name: c.schema() for c in COMMANDS}
    out.update(BENCH)
    return out


def phases_of(cmd, v):
    """Phases a step goes through, in order (the ids Reporter.phase() prints); [] = one phase, "run"."""
    if cmd in BY_NAME:
        return BY_NAME[cmd].phases_of(v)
    return BENCH_PHASES.get(cmd, [])


def report_of(cmd, v):
    """The page a step writes, or None."""
    return BY_NAME[cmd].report_of(v) if cmd in BY_NAME else None


def argv_of(cmd, values, sch):
    if cmd in BENCH_SCRIPTS:
        argv = [sys.executable, "-u", os.path.join(ROOT, *BENCH_SCRIPTS[cmd])]
    else:
        argv = [sys.executable, "-u", "-m", "paretogpu", cmd]
    for a in sch[cmd]["args"]:
        v = values.get(a["dest"])
        if v in (None, "", [], False):
            continue
        items = v if isinstance(v, list) else [v]
        if a["kind"] == "flag":
            argv.append(a["flag"])
        elif a["kind"] == "positional":
            argv += [str(x) for x in items]
        elif a["kind"] == "list":
            for x in items:
                argv += [a["flag"], str(x)]
        else:
            argv += [a["flag"], str(v)]
    return argv


def plan(preset_id, values, sch):
    """([(cmd, values of the step)], the snapshot folder the chain makes or None) of a preset; ValueError if it
    cannot run with these values."""
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if not preset:
        raise ValueError(f"unknown preset {preset_id}")
    frame_dir = None
    if preset_id == "frame_cost":  # the snapshot is made by the first step and priced by the second
        if not values.get("project"):
            raise ValueError("укажите Unity-проект")
        frame_dir = workspace.new_snapshot_dir(values["project"], values.get("suffix"))
    steps = []
    for st in preset["steps"]:
        cmd = st["cmd"]
        mine = {a["dest"] for a in sch[cmd]["args"]}
        v = {k: values[k] for k in mine if k in values and k not in st.get("hide", [])}
        if frame_dir:
            if cmd == "frame":
                v["out"] = frame_dir
            else:
                v["frame"] = frame_dir
                v["project"] = values["project"]
        if cmd in BY_NAME:
            v = BY_NAME[cmd].ui_values_of(v)
        missing = [a["flag"] or a["dest"] for a in sch[cmd]["args"]
                   if (a.get("required") or a["kind"] == "positional") and v.get(a["dest"]) in (None, "", [])]
        if missing:
            raise ValueError(f"{cmd}: не заполнено {', '.join(missing)}")
        steps.append((cmd, v))
    return steps, frame_dir
