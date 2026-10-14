"""Frame events -> variants compiled in the open editor and measured with malioc (the state core/variants.match()
reads). A device frame's SPIR-V is measured as it is, never compiled."""
import hashlib
import os
import shutil

from paretogpu.adapters.unity import variants as unity_variants
from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app.engine import Engine
from paretogpu.app.rules import CompileRule, LoopRule, MeasureRule
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.model.frame import Event
from paretogpu.model.variant import VariantKey, VariantState, slug
from paretogpu.store import memo as memo_store
from paretogpu.store import variant_store as store
from paretogpu.views.reporter import CONSOLE

NOT_COMPILED_YET = "not compiled yet (--no-compile)"
PASSES = "passes/2"


def shader_query(project, entry, shaders, root):
    return unity_variants.shader_query(project, entry, shaders, store.raw_dir(root))


def shader_passes(project, shaders, root, memo=None):
    """-> {shader name: passes or None (not found)}; memoized per shader fingerprint."""
    memo = memo or memo_store.default()
    names = sorted(set(shaders))
    fps = shader_query(project, "Fingerprints", names, root)
    key = {n: f"{n}|{fps[n]}" for n in names if fps.get(n)}
    out = {n: memo.get(PASSES, key[n]) for n in key}
    ask = [n for n in names if out.get(n) is None]
    if ask:
        got = shader_query(project, "Passes", ask, root)
        out.update({n: got.get(n) for n in ask})
        memo.put_many(PASSES, [(key[n], got[n]) for n in ask if n in key and got.get(n) is not None])
    return out


def identify(events: list[Event], frame_dir, root, rep=CONSOLE) -> int:
    """Matches device draws to project variants by byte-equal SPIR-V (Unity compiles the same code in the editor); ties
    -> fewest keywords. Depth-only draws match by vertex code alone, shared by several shaders (Unity names the module
    after the first), so the shader drawing the same mesh elsewhere wins; else unknown. -> number of draws matched."""
    draws = [ev for ev in events if (ev.get("code") or {}).get("vert") and ev["kind"] == "draw"]
    if not draws:
        return 0

    def digest(path):
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    pairs, verts = {}, {}
    for k, files in store.load_manifests(root).items():
        v, f = files.get(("vulkan", "vert")), files.get(("vulkan", "frag"))
        if v and f:
            dv = digest(os.path.join(root, v))
            pairs.setdefault((k.shader, dv, digest(os.path.join(root, f))), []).append((k, {"vert": v, "frag": f}))
            verts.setdefault(dv, []).append((k, {"vert": v}))

    def take(ev, found):
        k, files = min(found, key=lambda x: (len(x[0].keywords), x[0]))
        ev["code"].update(variant=k.to_list(), variants=len(found), files=files)
        ev.update(shader=k.shader, subshader=k.subshader, pass_index=k.pass_index, keywords=list(k.keywords))
        ev["pass"] = k.pass_name
    color, depth = [], []
    for ev in draws:
        code = ev["code"]
        if code.get("frag"):
            found = pairs.get((ev["shader"], digest(os.path.join(frame_dir, code["vert"])),
                               digest(os.path.join(frame_dir, code["frag"]))))
            if found:
                take(ev, found)
                color.append(ev)
        else:
            depth.append(ev)
    shaders_of = {}
    for ev in color:
        for m in ev.get("meshes") or []:
            shaders_of.setdefault(m, set()).add(ev["shader"])
    found_depth = 0
    for ev in depth:
        found = verts.get(digest(os.path.join(frame_dir, ev["code"]["vert"]))) or []
        drawn = set().union(*(shaders_of.get(m, set()) for m in ev.get("meshes") or []))
        mine = [x for x in found if x[0].shader in drawn] or (found if len({x[0].shader for x in found}) == 1 else [])
        if mine:
            take(ev, mine)
            found_depth += 1
    rep.log(f"  variants of the device frame: {len(color)} of {len(draws) - len(depth)} draws with a fragment shader "
            f"and {found_depth} of {len(depth)} depth-only draws have the code of a variant of the project")
    return len(color) + found_depth


def place_captured(events: list[Event], frame_dir, root) -> dict:
    """-> {VariantKey: {("vulkan", stage): file in root}}: identify()'s variant files, else the device SPIR-V copied
    once into <root>/<shader>_device/ (named by content). Never compiled: the code is the variant."""
    out = {}
    for ev in events:
        code = ev.get("code")
        if not code or ev["kind"] not in ("draw", "compute"):
            continue
        k = VariantKey.of(ev)
        if k in out:
            continue
        if code.get("files"):
            out[k] = {("vulkan", st): rel for st, rel in code["files"].items()}
            continue
        files = {}
        for s in ("vert", "frag", "comp"):
            if not code.get(s):
                continue
            rel = f"{slug(k.shader)}_device/{os.path.basename(code[s])}"
            dst = os.path.join(root, rel)
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(frame_dir, code[s]), dst + ".tmp")
                os.replace(dst + ".tmp", dst)
            files[("vulkan", s)] = rel
        out[k] = files
    return out


def run(events: list[Event], project, root, cores, platforms=("gles3", "vulkan"), jobs=None, compile_missing=True,
        rep=CONSOLE, recompile=False, retry_failed=False, captured=None) -> VariantState:
    """captured (place_captured()): measured as they are, not compiled."""
    root = os.path.abspath(root)
    project = project and os.path.abspath(project)
    captured = captured or {}
    keys = list(dict.fromkeys(VariantKey.of(e) for e in events if e["kind"] in ("draw", "compute")))
    engine = Engine(rep, jobs)
    compiling = CompileRule(project, root, platforms, compile_missing, recompile, retry_failed, rep)
    todo = [k for k in keys if k not in captured]
    compiled = engine.get(compiling, todo) if todo else {}
    compiled.update({k: {"files": dict(f), "errors": [] if f else ["no shader in the capture"]}
                     for k, f in captured.items() if k in keys})
    for v in compiled.values():
        v["files"] = {ps: fn for ps, fn in v["files"].items() if ps[0] in platforms}
    files = sorted({fn for v in compiled.values() for fn in v["files"].values()})
    measurements = {}
    if files:
        rep.log(f"measuring {root} on {', '.join(cores)} ...")
        recs = engine.get(MeasureRule(root), [(fn, c) for fn in files for c in cores])
        for (fn, c), r in recs.items():
            measurements.setdefault(fn, {})[c] = {k: v for k, v in r.items() if k != "cached"}
        failed = [k for k, r in recs.items() if not r["ok"]]
        if failed:
            rep.log(f"  malioc failed on {len(failed)} files")
    errors = {k: v["errors"] or ([NOT_COMPILED_YET] if v.get("pending") else []) for k, v in compiled.items()}
    return {"keys": keys, "files": {k: v["files"] for k, v in compiled.items()}, "errors": errors,
            "measurements": measurements, "checked": compiling.checked if todo else True,
            "captured": set(captured)}


def compile_keys(keys, project, root, platforms, rep=CONSOLE, compile_missing=True):
    """-> {key: {"files", "errors"}}; still-valid variants are kept."""
    return Engine(rep).get(CompileRule(project and os.path.abspath(project), os.path.abspath(root), platforms,
                                       compile_missing=compile_missing, rep=rep), keys)


def loops_of(state: VariantState, root, cores, jobs=None, rep=CONSOLE) -> dict:
    """-> {file: {core: LoopProfile}} only on cores where the longest path is N/A ({} if not forceable), so a file's
    price on one core does not depend on which other cores the run measures."""
    files = sorted({fn for k in state["keys"] for fn in state["files"].get(k, {}).values()})
    na = {(fn, c) for fn in files for c, r in state["measurements"].get(fn, {}).items()
          if c in cores and r.get("ok", True) and heavy.combined(r)["longest"] is None}
    dyn = sorted({fn for fn, _ in na})
    if not dyn:
        rep.skip("loops")
        return {}
    rep.log(f"pricing dynamic loops of {len(dyn)} files: every outer loop at 0 and 2 iterations, the others at 1 ...")
    res = Engine(rep, jobs).get(LoopRule(root), sorted(na))
    out = {fn: {} for fn in dyn}
    for (fn, core), p in res.items():
        if p:
            out[fn][core] = p
    bad = [fn for fn in dyn if any((fn, c) in na and c not in out[fn] for c in cores)]
    if bad:
        rep.log(f"  loops not forced in {len(bad)} files (priced by total): " + ", ".join(bad[:5])
                + (" ..." if len(bad) > 5 else ""))
    return out


def bounds_of(state: VariantState, project, root, compile_missing=True, rep=CONSOLE) -> dict:
    """-> {file: [bound per outer loop or None]}, read from the GLES file of the same variant (HLSLcc keeps uniform
    names, outer loops in the same order); device code has no GLES file: read from its SPIR-V. Files whose GLES file is
    missing or has another outer loop count are left out (default n)."""
    looped = {fn: next(iter(p.values())) for fn, p in (state.get("loops") or {}).items() if p}
    if not looped:
        rep.skip("bounds")
        return {}
    rep.phase("bounds")
    stage_of = {fn: (k, s) for k, files in state["files"].items() for (_, s), fn in files.items()}
    captured = state.get("captured") or set()
    keys = list(dict.fromkeys(stage_of[fn][0] for fn in looped if fn in stage_of and stage_of[fn][0] not in captured))
    gles = {}
    if keys:
        try:
            gles = compile_keys(keys, project, root, ["gles3"], rep, compile_missing and bool(project))
        except VariantsError as e:
            rep.log(f"  GLES files not compiled ({e}): the compiled ones are used without checking")
            gles = compile_keys(keys, project, root, ["gles3"], rep, False)
    out = {}
    for fn, profile in looped.items():
        k, s = stage_of.get(fn, (None, None))
        if k in captured:
            with open(os.path.join(root, fn), "rb") as f:
                b = loops.spirv_bounds(f.read())
        else:
            rel = ((gles.get(k) or {}).get("files") or {}).get(("gles3", s))
            if not rel:
                continue
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                b = loops.glsl_bounds(f.read())
        if b is not None and len(b) == len(profile["loops"]):
            out[fn] = b
    rep.log(f"  loop bounds of {len(out)} of {len(looped)} files: "
            f"{sum(1 for b in out.values() for x in b if x)} loops bounded by uniforms")
    return out
