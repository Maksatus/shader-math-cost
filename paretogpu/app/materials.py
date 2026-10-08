"""Materials of a Unity project -> their shader variants, compiled and measured (core/materials.py plans them).

  resolve(project, path)  a .mat file -> its shader, keywords and switched-off passes
  prepare(project, paths, root, cores, ...)  the materials' variants compiled and measured, dynamic loops priced
  common(prep, sides, ...)  the fields every result about materials has (features/matcompare.py, matshader.py)
"""
import os
import time
from collections import Counter

from paretogpu.adapters.unity import assets as materials
from paretogpu.adapters.unity import cli as unity
from paretogpu.app import variants
from paretogpu.core import materials as plan_
from paretogpu.core.materials import NS
from paretogpu.core.pricing import LOOP_ITERS
from paretogpu.model.cores import main_core
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


def resolve(project, path, guids=None):
    """A material file (absolute, or relative to the project) -> materials.parse_material() + path, shader."""
    full = path if os.path.isabs(path) else os.path.join(project, path)
    if not os.path.isfile(full):
        raise ValueError(f"no material {path}")
    m = materials.parse_material(full)
    m["path"] = os.path.relpath(full, project).replace("\\", "/")
    guids = guids if guids is not None else materials.shader_guids(project)
    m["shader"] = guids.get(m["guid"])
    if not m["guid"]:
        m["error"] = "у материала нет шейдера"
    elif m["guid"] == materials.BUILTIN_GUID:
        m["error"] = f"встроенный шейдер Unity (fileID {m['file_id']}): его исходника нет в проекте"
    elif not m["shader"]:
        m["error"] = f"шейдер {m['guid']} не найден в проекте"
    return m


def prepare(project, paths, root, cores, platforms=("gles3", "vulkan"), jobs=None, compile_missing=True,
            recompile=False, snapshot_folders=(), rep=CONSOLE):
    """Materials -> their variants (passes, material and pipeline keywords) -> compiled and measured, with the
    dynamic loops priced. Returns {"project", "mats", "keys" (per material, sorted by pass), "state", "snaps"}."""
    project = os.path.abspath(project)
    rep.phase("materials")
    guids = materials.shader_guids(project)
    mats = [resolve(project, x, guids) for x in paths]
    for side, m in zip("AB" if len(mats) > 1 else [""], mats):
        if m.get("error"):
            raise ValueError(f"{side + ' ' if side else ''}{m['path']}: {m['error']}")
    snaps = workspace.snapshots_of(project, list(snapshot_folders))
    info = plan_.snapshot_info(snaps)
    editor = None
    names = sorted({m["shader"] for m in mats})
    if compile_missing:
        if unity.editor_ready(project, allow_play=True):
            editor = variants.shader_query(project, "Passes", names, root)
    if editor is None:
        rep.log("passes are taken from the snapshots only "
                 + ("(--no-compile)" if not compile_missing else "(the editor does not answer Unity CLI)"))
    if not info["by_light_mode"] and editor:
        # no snapshot of the project: every pass that shades the picture, without global keywords
        info["by_light_mode"] = {p["light_mode"]: Counter({(): 1}) for ps in editor.values() for p in ps or []
                                 if p["light_mode"] not in plan_.UTILITY_LIGHT_MODES}
    sides = []
    all_keys = {}
    for m in mats:
        keys, skipped = plan_.plan([m], info, editor)
        if not keys:
            raise ValueError(f"{m['path']}: " + (skipped[0]["reason"] if skipped else "ни одного прохода"))
        all_keys.update(keys)
        sides.append(sorted(keys, key=lambda k: (k.subshader, k.pass_index)))
    rep.log(f"materials: {len(all_keys)} variants, {len(snaps)} snapshots of the project for global keywords")
    state = variants.run([k.as_event() for k in all_keys], project, root, cores, platforms, jobs,
                         compile_missing=compile_missing, recompile=recompile, rep=rep)
    state["loops"] = variants.loops_of(state, root, cores, jobs, rep)
    return {"project": project, "mats": mats, "keys": sides, "state": state, "snaps": snaps}


def common(prep, sides, cores, api, apis, ns=NS):
    """The fields every result of materials has: cores found, malioc, warnings."""
    state, snaps = prep["state"], prep["snaps"]
    found = [c for c in cores if any(c in p["prices"] for s in sides for p in s["passes"])]
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    warn = []
    for side, s in zip("AB" if len(sides) > 1 else [""], sides):
        for p in s["passes"]:
            if p["error"]:
                warn.append(f"{side + ', ' if side else ''}проход {p['pass']}: нет цены ({p['error']})")
    if not state["checked"]:
        warn.append("Варианты не сверены с шейдерами (редактор не отвечал или --no-compile): цены могут быть устаревшими.")
    if not snaps:
        warn.append("В проекте нет снимков кадра: глобальные keywords пайплайна (свет, тени, Forward+) не добавлены, "
                    "а проходы взяты все, кроме служебных.")
    return {"project": prep["project"], "api": api, "apis": apis, "cores": found,
            "main_core": main_core(found), "ns": list(ns),
            "default_n": LOOP_ITERS, "malioc": ", ".join(sorted({r["malioc"] for r in recs})),
            "checked": state["checked"], "snapshots": len(snaps), "warnings": warn, "computed_at": time.time()}
