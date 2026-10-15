import os
import sys

from paretogpu.adapters.unity.cli import android_package
from paretogpu.features import BY_NAME, COMMANDS
from paretogpu.model.phases import labels as phase_labels
from paretogpu.store import workspace

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

# one button = steps run in order; `hide`: args the server fills in (the chain's own folders)
PRESETS = [
    {"id": "frame_cost", "title": "Проверка кадра",
     "steps": [{"cmd": "frame", "hide": ["out"]}, {"cmd": "cost", "hide": ["frame", "out", "project"]}]},
    {"id": "cost", "title": "Пересчёт снимка", "steps": [{"cmd": "cost"}]},
    {"id": "site", "title": "Обновление данных сайта", "steps": [{"cmd": "bench"}]},
    {"id": "matcompare", "title": "Сравнение материалов", "steps": [{"cmd": "matcompare"}]},
]


def schema():
    return {c.name: c.schema() for c in COMMANDS}


def phases_of(cmd, v):
    """[] -> the runner uses one phase, "run"."""
    return BY_NAME[cmd].phases_of(v) if cmd in BY_NAME else []


def report_of(cmd, v):
    return BY_NAME[cmd].report_of(v) if cmd in BY_NAME else None


def kind_of(cmd):
    r = BY_NAME[cmd].result if cmd in BY_NAME else None
    return r.name if r else None


def argv_of(cmd, values, sch):
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
    """-> ([(cmd, values)], snapshot dir the chain makes or None); ValueError if it cannot run."""
    preset = next((p for p in PRESETS if p["id"] == preset_id), None)
    if not preset:
        raise ValueError(f"unknown preset {preset_id}")
    frame_dir = None
    phone = (values.get("project") or "").startswith("device:")
    first = []
    if preset_id == "frame_cost" and phone:  # `device` replaces the `frame` step
        serial, _, project = values["project"][len("device:"):].partition(":")
        frame_dir = workspace.new_snapshot_dir(project, "_".join(x for x in (values.get("suffix"), "device") if x))
        pkg = android_package(project)
        if not pkg:
            raise ValueError("нет Android-пакета проекта (ProjectSettings: applicationIdentifier)")
        first = [("device", {"frame": frame_dir, "project": project, "app": pkg, "serial": serial,
                             "wait": values.get("wait")})]
        values = {**values, "project": project}
    elif preset_id == "frame_cost":
        if not values.get("project"):
            raise ValueError("укажите Unity-проект")
        frame_dir = workspace.new_snapshot_dir(values["project"], values.get("suffix"))
    steps = list(first)
    for st in preset["steps"]:
        if first and st["cmd"] == "frame":
            continue
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
                   if (a["required"] or a["kind"] == "positional") and v.get(a["dest"]) in (None, "", [])]
        if missing:
            raise ValueError(f"{cmd}: не заполнено {', '.join(missing)}")
        steps.append((cmd, v))
    return steps, frame_dir


def ui_schema():
    return {"commands": schema(), "presets": PRESETS, "phases": phase_labels()}
