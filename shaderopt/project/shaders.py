"""Shader variants of the project's materials, priced like the frame (the "project shaders" tab of the frame report).

Every material selects variants: its shader, the color passes it is drawn with and its keywords plus the global
keywords the pipeline sets for that pass. Passes come from the open editor (ShaderoptVariants.Passes: the passes
whose LightMode the snapshots draw in color, minus the material's disabled passes); without the editor, from the
frame snapshots of the project (only the passes they drew). Global keywords: the ones the snapshots saw with that
shader and pass, else the ones most often seen with that LightMode. Variants are compiled and measured like the frame's (frame/variants.py) and rows
are the distinct compiled shaders: materials whose keywords give the same code share a row. A row's frame share is
the share of the frame events that ran the same code.
"""
import os
from collections import Counter, defaultdict

from shaderopt.frame import cost as frame_cost
from shaderopt.frame import variants

COLOR_STAGES = ("opaque", "transparent")


def snapshot_info(snapshots):
    """From the events of the project's snapshots: {"passes": {shader: {(subshader, pass_index, pass, light_mode)}},
    "globals": {(shader, pass): Counter(global keywords)}, "by_light_mode": {light_mode: Counter(global keywords)}}."""
    passes, glob, by_lm = defaultdict(set), defaultdict(Counter), defaultdict(Counter)
    for events in snapshots:
        for e in events:
            if e["kind"] != "draw" or e["stage"] not in COLOR_STAGES or not e.get("shader"):
                continue
            g = tuple(sorted(e.get("global_keywords") or []))
            passes[e["shader"]].add((e["subshader"] or 0, e["pass_index"] or 0, e["pass"] or "", e.get("light_mode")))
            glob[(e["shader"], e["pass"] or "")][g] += 1
            by_lm[e.get("light_mode")][g] += 1
    return {"passes": passes, "globals": glob, "by_light_mode": by_lm}


def material_shaders(materials):
    return sorted({m["shader"] for m in materials if not m.get("error")})


def plan(materials, info, editor_passes=None):
    """({variant key: [material paths]}, [skipped: {"material", "shader", "reason"}]).
    editor_passes: {shader: [{"subshader", "pass_index", "pass", "light_mode"}] or None} from the open editor."""
    color_lms = set(info["by_light_mode"])
    keys, skipped = defaultdict(list), []
    for m in materials:
        if m.get("error"):
            skipped.append({"material": m["path"], "shader": m.get("shader"), "reason": m["error"]})
            continue
        sh = m["shader"]
        got = (editor_passes or {}).get(sh)
        if got:
            # the subshader the snapshots drew with, else the first one
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
        off = set(m.get("disabled_passes") or [])
        passes = [p for p in passes if p[3] not in off and p[2] not in off]
        if not passes:
            why = ("no color pass the snapshots draw" if got else
                   "its passes are not in the snapshots (the open editor lists all of them)")
            skipped.append({"material": m["path"], "shader": sh, "reason": why})
            continue
        for sub, idx, name, lm in passes:
            g = info["globals"].get((sh, name)) or info["by_light_mode"].get(lm) or Counter({(): 1})
            kw = tuple(sorted(set(m["keywords"]) | set(g.most_common(1)[0][0])))
            keys[(sh, sub, idx, name, kw)].append(m["path"])
    return dict(keys), skipped


def pseudo_events(keys):
    """Draw events that name the variants, for frame/variants.run()."""
    return [{"index": -1, "kind": "draw", "shader": k[0], "subshader": k[1], "pass_index": k[2], "pass": k[3],
             "keywords": list(k[4])} for k in keys]


def _code(state, files, core):
    """Identity of the compiled code of a variant on one core: the source hashes of its stages."""
    out = []
    for stage in ("fragment", "vertex"):
        rec = state["measurements"].get(files.get(stage), {}).get(core) or {}
        out.append(rec.get("source_sha1"))
    return tuple(out) if all(out) else None


def rows(keys, skipped, state, cost, api="vulkan", loop_iters=frame_cost.LOOP_ITERS, loop_overrides=None):
    """The project shaders tab: one row per distinct compiled shader of the materials' variants."""
    cores = [c["name"] for c in cost["cores"]]
    main = cost["main_core"]
    loop_data = state.get("loops") or {}
    # frame share of every compiled shader (events that ran the same code)
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
        ev = pseudo_events([k])[0]
        recs, why = variants.match(ev, state, api)
        if not recs or "fragment" not in recs or "vertex" not in recs:
            unpriced.append({"shader": k[0], "pass": k[3], "keywords": list(k[4]), "materials": mats,
                             "reason": why or "not compiled"})
            continue
        files = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
        code = _code(state, files, main) or (k,)
        row = out.get(code)
        if row is None:
            n, fixed = frame_cost.loop_n(k[0], loop_iters, loop_overrides)
            prices = {}
            for c in cores:
                if c not in recs["fragment"] or c not in recs["vertex"]:
                    continue
                f = frame_cost.price(recs["fragment"], files["fragment"], c, n, loop_data)
                v = frame_cost.price(recs["vertex"], files["vertex"], c, n, loop_data)
                prices[c] = {"px_price": f["cycles"], "px_bound": f["bound"], "px_path": f["path"],
                             "vtx_price": v["cycles"], "vtx_bound": v["bound"], "regs": f["work_regs"],
                             "fp16": f["fp16_pct"], "flags": sorted(set(f["flags"]) | {x for x in v["flags"] if x != "low_fp16"})}
            fr = in_frame.get(code)
            row = out[code] = {"shader": k[0], "pass": k[3], "keyword_sets": [], "materials": [], "prices": prices,
                               "in_frame": dict(fr["share"]) if fr else {}, "frame_events": fr["events"] if fr else 0,
                               "frame_pixels": fr["pixels"] if fr else 0, "files": files}
        row["keyword_sets"].append(list(k[4]))
        row["materials"] += [m for m in mats if m not in row["materials"]]
    result = sorted(out.values(), key=lambda r: -(r["prices"].get(main, {}).get("px_price") or 0))
    for r in result:
        r["keyword_sets"].sort(key=len)
    return {"rows": result, "unpriced": unpriced, "skipped": skipped,
            "materials": len({m for v in keys.values() for m in v}), "variants": len(keys)}


def snapshots_of(project, folders):
    """Events of every frame_events.json under `folders` (one level of subfolders) taken in `project`."""
    import json
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
