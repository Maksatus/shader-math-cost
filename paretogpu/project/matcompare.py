"""Two materials of a Unity project against each other: the price of their shader variants per pixel and vertex.

A material selects its variants (project/shaders.py): its shader, the color passes it is drawn with, its keywords
plus the global keywords the pipeline sets for that pass (seen in the project's snapshots). Both materials'
variants are compiled in the open editor and measured with malioc like a frame's (frame/variants.py, the same
variants folder of the project, so a variant is compiled once), and dynamic loops (lights, probes, ray steps) are
priced at every n of NS (frame/loops.py). No frame is needed: the result is the price of one pixel / vertex.

Passes of A and B are paired by name, the rest in order (another shader may name its passes differently).

  run(project, a, b, root, cores, ...) -> JSON-able result; render_html() puts it into frame/compare_view.html
"""
import json
import os
import time
from collections import Counter

from paretogpu import progress as progress_ui
from paretogpu.frame import compare
from paretogpu.frame import cost as frame_cost
from paretogpu.frame import loops
from paretogpu.frame import variants
from paretogpu.profile import score as heavy
from paretogpu.project import materials, shaders

NS = frame_cost.LOOP_NS
# passes that do not shade the picture: used only when no snapshot says which LightModes draw color
UTILITY_LIGHT_MODES = {"ShadowCaster", "DepthOnly", "DepthNormals", "DepthNormalsOnly", "Meta", "MotionVectors",
                       "SceneSelectionPass", "Picking", "Universal2D"}


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


def _pairs(pa, pb):
    """Indices of the passes of A and B paired: by name, then the rest in order; None where a pass has no partner."""
    out, rest_a, rest_b = [], [], list(range(len(pb)))
    for i, x in enumerate(pa):
        j = next((j for j in rest_b if pb[j] == x), None)
        if j is None:
            rest_a.append(i)
        else:
            rest_b.remove(j)
            out.append([i, j])
    while rest_a or rest_b:
        out.append([rest_a.pop(0) if rest_a else None, rest_b.pop(0) if rest_b else None])
    return sorted(out, key=lambda p: (len(pa) if p[0] is None else p[0], p[1] or 0))


def _round(c):
    return c and {p: round(v, 4) for p, v in c.items() if v is not None}


def _pipes(rec, file, core, ns, loop_data):
    """Cycles of every pipe of one stage: longest path at every n (dynamic loops forced, frame/loops.py; without
    them the same at every n, or total raised to shortest where malioc has no longest), shortest, total."""
    c = heavy.combined(rec)
    p = (loop_data.get(file) or {}).get(core)
    longest = c["longest"] if c["longest"] is not None else heavy.fallback(c)
    return {"longest": {str(n): _round(loops.cycles_at(p, n) if p else longest) for n in ns},
            "longest_na": c["longest"] is None, "shortest": _round(c["shortest"]), "total": _round(c["total"])}


def _price_pass(k, state, cores, api, loop_data, ns):
    """Prices of one variant on every core at every n, or (None, reason)."""
    recs, why = variants.match(shaders.pseudo_events([k])[0], state, api)
    if not recs or "fragment" not in recs or "vertex" not in recs:
        return None, why or "не скомпилирован"
    files = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
    out = {}
    for c in cores:
        if c not in recs["fragment"] or c not in recs["vertex"]:
            continue
        looped = bool((loop_data.get(files["fragment"]) or {}).get(c) or (loop_data.get(files["vertex"]) or {}).get(c))
        px, vtx = {}, {}
        for n in ns:
            f = frame_cost.price(recs["fragment"], files["fragment"], c, n, loop_data)
            v = frame_cost.price(recs["vertex"], files["vertex"], c, n, loop_data)
            px[str(n)], vtx[str(n)] = f["cycles"], v["cycles"]
        f = frame_cost.price(recs["fragment"], files["fragment"], c, frame_cost.LOOP_ITERS, loop_data)
        v = frame_cost.price(recs["vertex"], files["vertex"], c, frame_cost.LOOP_ITERS, loop_data)
        out[c] = {"px": px, "vtx": vtx, "looped": looped,
                  "px_pipes": _pipes(recs["fragment"][c], files["fragment"], c, ns, loop_data),
                  "vtx_pipes": _pipes(recs["vertex"][c], files["vertex"], c, ns, loop_data), "px_bound": f["bound"], "vtx_bound": v["bound"],
                  "px_path": f["path"], "vtx_path": v["path"], "px_regs": f["work_regs"], "vtx_regs": v["work_regs"],
                  "px_fp16": f["fp16_pct"],
                  "flags": sorted(set(f["flags"]) | {x for x in v["flags"] if x != "low_fp16"})}
    return {"files": files, "prices": out}, None


def run(project, a, b, root, cores, platforms=("gles3", "vulkan"), api="vulkan", jobs=None, compile_missing=True,
        recompile=False, ns=NS, snapshot_folders=(), progress=print):
    project = os.path.abspath(project)
    progress_ui.phase("materials")
    guids = materials.shader_guids(project)
    mats = [resolve(project, a, guids), resolve(project, b, guids)]
    for side, m in zip("AB", mats):
        if m.get("error"):
            raise ValueError(f"{side} {m['path']}: {m['error']}")
    snaps = shaders.snapshots_of(project, list(snapshot_folders))
    info = shaders.snapshot_info(snaps)
    editor = None
    names = sorted({m["shader"] for m in mats})
    if compile_missing:
        from paretogpu.unity import export as unity
        if unity.editor_ready(project, allow_play=True):
            editor = variants.shader_query(project, "Passes", names, root)
    if editor is None:
        progress("passes are taken from the snapshots only "
                 + ("(--no-compile)" if not compile_missing else "(the editor does not answer Unity CLI)"))
    if not info["by_light_mode"] and editor:
        # no snapshot of the project: every pass that shades the picture, without global keywords
        info["by_light_mode"] = {p["light_mode"]: Counter({(): 1}) for ps in editor.values() for p in ps or []
                                 if p["light_mode"] not in UTILITY_LIGHT_MODES}
    sides = []
    all_keys = {}
    for m in mats:
        keys, skipped = shaders.plan([m], info, editor)
        if not keys:
            raise ValueError(f"{m['path']}: " + (skipped[0]["reason"] if skipped else "ни одного прохода"))
        all_keys.update(keys)
        sides.append(sorted(keys, key=lambda k: (k[1], k[2])))
    progress(f"materials: {len(all_keys)} variants, {len(snaps)} snapshots of the project for global keywords")
    state = variants.run(shaders.pseudo_events(list(all_keys)), project, root, cores, platforms, jobs,
                         compile_missing=compile_missing, recompile=recompile, progress=progress)
    state["loops"] = loops.for_frame(state, root, cores, jobs, progress)
    progress_ui.phase("report")
    # prices of both APIs when both were compiled: the main one first
    apis = [api] + [x for x in (variants.API[p] for p in platforms) if x != api]
    out_sides = []
    for m, keys in zip(mats, sides):
        passes = []
        for k in keys:
            by_api, errors = {}, {}
            for x in apis:
                priced, errors[x] = _price_pass(k, state, cores, x, state["loops"], ns)
                if priced:
                    by_api[x] = priced
            main = by_api.get(api) or {"files": {}, "prices": {}}
            passes.append({"pass": k[3], "subshader": k[1], "pass_index": k[2], "keywords": list(k[4]),
                           "global_keywords": sorted(set(k[4]) - set(m["keywords"])),
                           "variant": variants.key_str(k), "error": errors[api], **main,
                           "prices_by_api": {x: v["prices"] for x, v in by_api.items()},
                           "errors_by_api": {x: e for x, e in errors.items() if e}})
        out_sides.append({"material": m["name"], "path": m["path"], "shader": m["shader"], "keywords": m["keywords"],
                          "disabled_passes": m["disabled_passes"], "passes": passes})
    found = [c for c in cores if any(c in p["prices"] for s in out_sides for p in s["passes"])]
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    warn = []
    for side, s in zip("AB", out_sides):
        for p in s["passes"]:
            if p["error"]:
                warn.append(f"{side}, проход {p['pass']}: нет цены ({p['error']})")
    if not state["checked"]:
        warn.append("Варианты не сверены с шейдерами (редактор не отвечал или --no-compile): цены могут быть устаревшими.")
    if not snaps:
        warn.append("В проекте нет снимков кадра: глобальные keywords пайплайна (свет, тени, Forward+) не добавлены, "
                    "а проходы взяты все, кроме служебных.")
    return {"kind": "materials", "project": project, "api": api, "apis": apis, "cores": found,
            "main_core": "Mali-G78" if "Mali-G78" in found else (found[0] if found else None), "ns": list(ns),
            "default_n": frame_cost.LOOP_ITERS, "a": out_sides[0], "b": out_sides[1],
            "pairs": _pairs([p["pass"] for p in out_sides[0]["passes"]], [p["pass"] for p in out_sides[1]["passes"]]),
            "malioc": ", ".join(sorted({r["malioc"] for r in recs})), "checked": state["checked"],
            "snapshots": len(snaps), "warnings": warn, "computed_at": time.time()}


def render_html(res):
    return compare.render_html(res, f"Материалы: {res['a']['material']} → {res['b']['material']}")


def write(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "matcompare.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "matcompare.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_html(res))
    return os.path.join(out_dir, "matcompare.html")
