"""Project materials' shader variants priced like the frame. A variant = material keywords + the global keywords seen
with that shader and pass in snapshots (else the most common for its LightMode). Passes: the open editor's
(ParetoGpuVariants.Passes, color LightModes only), else the ones the snapshots drew. Rows = distinct compiled code.
"""
from collections import Counter, defaultdict

from paretogpu.core import pricing as heavy
from paretogpu.core import loops
from paretogpu.core.pricing import LOOP_ITERS, LOOP_NS, price, price_draw
from paretogpu.core.variants import match
from paretogpu.model.variant import API, VariantKey

COLOR_STAGES = ("opaque", "transparent")
NS = LOOP_NS
# non-color passes; used only when no snapshot says which LightModes draw color
UTILITY_LIGHT_MODES = {"ShadowCaster", "DepthOnly", "DepthNormals", "DepthNormalsOnly", "Meta", "MotionVectors",
                       "SceneSelectionPass", "Picking", "Universal2D"}


def snapshot_info(snapshots, own=None):
    """-> {"passes": {shader: {(subshader, pass_index, pass, light_mode)}}, "globals": {(shader, pass): Counter},
    "by_light_mode": {light_mode: Counter}, "own": the same of `own` or None} of color draws; Counter keys are sorted
    global keyword tuples. own: the priced frame, whose global keywords win over other snapshots (other quality
    levels, e.g. the editor's Forward+)."""
    passes, glob, by_lm = defaultdict(set), defaultdict(Counter), defaultdict(Counter)
    for events in snapshots + ([own] if own else []):
        for e in events:
            if e["kind"] != "draw" or e["stage"] not in COLOR_STAGES or not e.get("shader"):
                continue
            g = tuple(sorted(e.get("global_keywords") or []))
            passes[e["shader"]].add((e["subshader"] or 0, e["pass_index"] or 0, e["pass"] or "", e.get("light_mode")))
            glob[(e["shader"], e["pass"] or "")][g] += 1
            by_lm[e.get("light_mode")][g] += 1
    return {"passes": passes, "globals": glob, "by_light_mode": by_lm, "own": own and snapshot_info([own])}


def material_shaders(materials):
    return sorted({m["shader"] for m in materials if not m.get("error")})


def plan(materials, info, editor_passes=None):
    """-> ({VariantKey: [material paths]}, [{"material", "shader", "reason"}]).
    editor_passes: {shader: [{"subshader", "pass_index", "pass", "light_mode", "keywords"}] or None}. Material keywords
    the pass does not declare are dropped. As in Unity, disabled_passes match LightMode case-insensitively and a pass
    without one is SRPDEFAULTUNLIT."""
    color_lms = set(info["by_light_mode"])
    keys, skipped = defaultdict(list), []
    for m in materials:
        if m.get("error"):
            skipped.append({"material": m["path"], "shader": m.get("shader"), "reason": m["error"]})
            continue
        sh = m["shader"]
        got = (editor_passes or {}).get(sh)
        if got:
            seen_sub = {p[0] for p in info["passes"].get(sh, ())}
            sub = min(seen_sub) if seen_sub else min(p["subshader"] for p in got)
            passes = [(p["subshader"], p["pass_index"], p["pass"], p["light_mode"]) for p in got
                      if p["subshader"] == sub and p["light_mode"] in color_lms]
        elif sh in info["passes"]:
            passes = sorted(info["passes"][sh], key=lambda p: (p[0], p[1]))
        elif editor_passes is None:
            skipped.append({"material": m["path"], "shader": sh,
                            "reason": "not in any snapshot; its passes need the open editor"})
            continue
        else:
            skipped.append({"material": m["path"], "shader": sh, "reason": "the editor did not find the shader"})
            continue
        off = {x.lower() for x in m.get("disabled_passes") or []}
        passes = [p for p in passes if (p[3] or "SRPDEFAULTUNLIT").lower() not in off]
        declared = {(p["subshader"], p["pass_index"]): p.get("keywords") for p in got or []}
        if not passes:
            why = ("no color pass the snapshots draw" if got else
                   "its passes are not in the snapshots (the open editor lists all of them)")
            skipped.append({"material": m["path"], "shader": sh, "reason": why})
            continue
        for sub, idx, name, lm in passes:
            o = info.get("own") or {"globals": {}, "by_light_mode": {}}
            g = (o["globals"].get((sh, name)) or o["by_light_mode"].get(lm) or info["globals"].get((sh, name))
                 or info["by_light_mode"].get(lm) or Counter({(): 1}))
            own = declared.get((sub, idx))
            mine = set(m["keywords"]) & set(own) if own is not None else set(m["keywords"])
            kw = tuple(sorted(mine | set(g.most_common(1)[0][0])))
            keys[VariantKey(sh, sub, idx, name, kw)].append(m["path"])
    return dict(keys), skipped


def _code(state, files, core):
    out = []
    for stage in ("fragment", "vertex"):
        rec = state["measurements"].get(files.get(stage), {}).get(core) or {}
        out.append(rec.get("source_sha1"))
    return tuple(out) if all(out) else None


def rows(keys, skipped, state, cost, api="vulkan"):
    cores = [c["name"] for c in cost["cores"]]
    main = cost["main_core"]
    loop_data = state.get("loops") or {}
    in_frame = defaultdict(lambda: {"share": defaultdict(float), "events": 0, "pixels": 0})
    for ev in cost["events"]:
        if ev["kind"] != "draw" or not ev["cost"] or "fragment" not in ev["files"]:
            continue
        code = _code(state, ev["files"], main)
        if code:
            f = in_frame[code]
            f["events"] += 1
            f["pixels"] += ev["pixels"]
            for c, x in ev["cost"].items():
                f["share"][c] += x["share"]
    out, unpriced = {}, []
    for k, mats in keys.items():
        ev = k.as_event()
        recs, why = match(ev, state, api)
        if not recs or "fragment" not in recs or "vertex" not in recs:
            unpriced.append({"shader": k.shader, "pass": k.pass_name, "keywords": list(k.keywords), "materials": mats,
                             "reason": why or "not compiled"})
            continue
        files = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
        code = _code(state, files, main) or (k,)
        row = out.get(code)
        if row is None:
            prices = {}
            for c in cores:
                if c not in recs["fragment"] or c not in recs["vertex"]:
                    continue
                d = price_draw(recs, files, c, LOOP_ITERS, loop_data)
                f, v = d["px"], d["vtx"]
                prices[c] = {"px_price": f["cycles"], "px_bound": f["bound"], "px_path": f["path"],
                             "vtx_price": v["cycles"], "vtx_bound": v["bound"], "regs": f["work_regs"],
                             "fp16": f["fp16_pct"], "flags": d["flags"]}
            fr = in_frame.get(code)
            row = out[code] = {"shader": k.shader, "pass": k.pass_name, "keyword_sets": [], "materials": [], "prices": prices,
                               "in_frame": dict(fr["share"]) if fr else {}, "frame_events": fr["events"] if fr else 0,
                               "frame_pixels": fr["pixels"] if fr else 0, "files": files}
        row["keyword_sets"].append(list(k.keywords))
        row["materials"] += [m for m in mats if m not in row["materials"]]
    result = sorted(out.values(), key=lambda r: -(r["prices"].get(main, {}).get("px_price") or 0))
    for r in result:
        r["keyword_sets"].sort(key=len)
    return {"rows": result, "unpriced": unpriced, "skipped": skipped,
            "materials": len({m for v in keys.values() for m in v}), "variants": len(keys)}


def pair_passes(pa, pb):
    """-> [[i, j]]: paired by equal name, then the rest in order; None = no partner."""
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
    c =heavy.combined(rec)
    p = (loop_data.get(file) or {}).get(core)
    longest = c["longest"] if c["longest"] is not None else heavy.fallback(c)
    return {"longest": {str(n): _round(loops.cycles_at(p, n) if p else longest) for n in ns},
            "longest_na": c["longest"] is None, "shortest": _round(c["shortest"]), "total": _round(c["total"])}


def _price_pass(k, state, cores, api, loop_data, ns):
    recs,why = match(k.as_event(), state, api)
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
            f = price(recs["fragment"], files["fragment"], c, n, loop_data)
            v = price(recs["vertex"], files["vertex"], c, n, loop_data)
            px[str(n)], vtx[str(n)] = f["cycles"], v["cycles"]
        d = price_draw(recs, files, c, LOOP_ITERS, loop_data)
        f, v = d["px"], d["vtx"]
        out[c] = {"px": px, "vtx": vtx, "looped": looped,
                  "px_pipes": _pipes(recs["fragment"][c], files["fragment"], c, ns, loop_data),
                  "vtx_pipes": _pipes(recs["vertex"][c], files["vertex"], c, ns, loop_data), "px_bound": f["bound"], "vtx_bound": v["bound"],
                  "px_path": f["path"], "vtx_path": v["path"], "px_regs": f["work_regs"], "vtx_regs": v["work_regs"],
                  "px_fp16": f["fp16_pct"], "flags": d["flags"]}
    return {"files": files, "prices": out}, None


def describe(m, keys, state, cores, apis, ns=NS):
    passes = []
    for k in keys:
        by_api, errors = {}, {}
        for x in apis:
            priced, errors[x] = _price_pass(k, state, cores, x, state["loops"], ns)
            if priced:
                by_api[x] = priced
        main = by_api.get(apis[0]) or {"files": {}, "prices": {}}
        passes.append({"pass": k.pass_name, "subshader": k.subshader, "pass_index": k.pass_index,
                       "keywords": list(k.keywords), "global_keywords": sorted(set(k.keywords) - set(m["keywords"])),
                       "variant": str(k), "key": k.to_list(),
                       "error": errors[apis[0]], **main,
                       "prices_by_api": {x: v["prices"] for x, v in by_api.items()},
                       "files_by_api": {x: v["files"] for x, v in by_api.items()},
                       "errors_by_api": {x: e for x, e in errors.items() if e}})
    return {"material": m["name"], "path": m["path"], "shader": m["shader"], "keywords": m["keywords"],
            "disabled_passes": m["disabled_passes"], "passes": passes}


def apis_of(api, platforms):
    return [api] + [x for x in (API[p] for p in platforms) if x != api]
