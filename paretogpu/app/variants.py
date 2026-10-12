"""Frame events -> compiled and measured shader variants (plan item K1.4).

Every draw of a Frame Debugger snapshot (frame_events.json) names its exact variant: shader, subshader,
pass index and the keywords it ran with. Those variants are compiled in the open editor into the variants folder of
the project and measured with malioc on the chosen cores; the dynamic loops of the files whose longest path is N/A
are priced at n iterations. What is still valid is not built again (app/rules.py, app/engine.py): a compiled variant
while its shader's fingerprint is the same, a malioc run of the same text from its cache.

run() -> the state core/variants.match() reads: per draw event, the measurement records of its fragment and vertex
shader (per compute event, of its kernel) on every core, or the reason there is none. loops_of() adds the prices of
the dynamic loops (core/loops.py) of the files whose longest path is N/A; bounds_of() what bounds each of their outer
loops (a function of the shader's uniforms, read from the GLES file of the same variant).
"""
import os

from paretogpu.adapters.unity import variants as unity_variants
from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app.engine import Engine
from paretogpu.app.rules import CompileRule, LoopRule, MeasureRule
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.model.frame import Event
from paretogpu.model.variant import VariantKey, VariantState
from paretogpu.store import memo as memo_store
from paretogpu.store import variant_store as store
from paretogpu.views.reporter import CONSOLE

NOT_COMPILED_YET = "not compiled yet (--no-compile)"
PASSES = "passes/1"


def shader_query(project, entry, shaders, root):
    return unity_variants.shader_query(project, entry, shaders, store.raw_dir(root))


def shader_passes(project, shaders, root, memo=None):
    """{shader name: its passes (None: not found)} from the open editor. The passes of a shader are remembered with
    its fingerprint (store/memo.py): only the shaders that changed since are asked for them."""
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


def run(events: list[Event], project, root, cores, platforms=("gles3", "vulkan"), jobs=None, compile_missing=True,
        rep=CONSOLE, recompile=False, retry_failed=False) -> VariantState:
    """Compile and measure the variants of the frame's draws. Returns the state for core/variants.match():
    {"keys", "files": {key: {(platform, stage): file}}, "errors": {key: [..]}, "measurements",
     "checked": True if the compiled files were checked against the shaders in the open editor}.
    recompile: compile every variant of the frame again; retry_failed: also the ones that failed before."""
    root = os.path.abspath(root)
    project = project and os.path.abspath(project)
    keys = list(dict.fromkeys(VariantKey.of(e) for e in events if e["kind"] in ("draw", "compute")))
    engine = Engine(rep, jobs)
    compiling = CompileRule(project, root, platforms, compile_missing, recompile, retry_failed, rep)
    compiled = engine.get(compiling, keys)
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
            "measurements": measurements, "checked": compiling.checked}


def compile_keys(keys, project, root, platforms, rep=CONSOLE):
    """Compile `keys` for `platforms` in the open editor, what is valid is kept: {key: {"files", "errors"}}."""
    return Engine(rep).get(CompileRule(project and os.path.abspath(project), os.path.abspath(root), platforms, rep=rep),
                           keys)


def loops_of(state: VariantState, root, cores, jobs=None, rep=CONSOLE) -> dict:
    """Loop prices of every measured file of the variants whose longest path is N/A on some core:
    {relative file: {core: LoopProfile}}; files whose loops cannot be forced map to {}."""
    files = sorted({fn for k in state["keys"] for fn in state["files"].get(k, {}).values()})
    dyn = [fn for fn in files
           if any(r.get("ok", True) and heavy.combined(r)["longest"] is None
                  for r in state["measurements"].get(fn, {}).values())]
    if not dyn:
        rep.skip("loops")
        return {}
    rep.log(f"pricing dynamic loops of {len(dyn)} files: every outer loop at 0 and 2 iterations, the others at 1 ...")
    res = Engine(rep, jobs).get(LoopRule(root), [(fn, c) for fn in dyn for c in cores])
    out = {fn: {} for fn in dyn}
    for (fn, core), p in res.items():
        if p:
            out[fn][core] = p
    bad = [fn for fn in dyn if len(out[fn]) < len(cores)]
    if bad:
        rep.log(f"  loops not forced in {len(bad)} files (priced by total): " + ", ".join(bad[:5])
                + (" ..." if len(bad) > 5 else ""))
    return out


def bounds_of(state: VariantState, project, root, compile_missing=True, rep=CONSOLE) -> dict:
    """{file: [bound per outer loop (core/loops.glsl_bounds) or None]} of the files with loop prices: the bounds are
    read from the GLES file of the same variant (HLSLcc keeps the uniform names; its outer loops are in the order of
    the other APIs'), compiled now in the open editor when missing and compile_missing. A file whose GLES file is
    missing or has another number of outer loops is left out: its loops keep the default n."""
    looped = {fn: next(iter(p.values())) for fn, p in (state.get("loops") or {}).items() if p}
    if not looped:
        rep.skip("bounds")
        return {}
    rep.phase("bounds")
    missing = [fn for fn in looped if not store.gles_file(root, fn)]
    if missing and compile_missing and project:
        key_of = {fn: k for k, files in state["files"].items() for fn in files.values()}
        keys = list(dict.fromkeys(key_of[fn] for fn in missing if fn in key_of))
        rep.log(f"compiling the GLES files of {len(keys)} variants with dynamic loops (their loop bounds) ...")
        try:
            compile_keys(keys, project, root, ["gles3"], rep)
        except VariantsError as e:
            rep.log(f"  not compiled: {e}")
    out = {}
    for fn, profile in looped.items():
        gf = store.gles_file(root, fn)
        if not gf:
            continue
        with open(gf, encoding="utf-8", errors="replace") as f:
            b = loops.glsl_bounds(f.read())
        if b is not None and len(b) == len(profile["loops"]):
            out[fn] = b
    rep.log(f"  loop bounds of {len(out)} of {len(looped)} files: "
            f"{sum(1 for b in out.values() for x in b if x)} loops bounded by uniforms")
    return out
