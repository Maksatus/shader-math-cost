"""Frame events -> compiled and measured shader variants (plan item K1.4).

Every draw of a Frame Debugger snapshot (frame_events.json) names its exact variant: shader, subshader,
pass index and the keywords it ran with. Those variants are compiled in the open editor into the variants folder of
the project and measured with malioc on the chosen cores; the dynamic loops of the files whose longest path is N/A
are priced at n iterations. What is still valid is not built again (app/rules.py, app/engine.py): a compiled variant
while its shader's fingerprint is the same, a malioc run of the same text from its cache.

run() -> the state core/variants.match() reads: per draw event, the measurement records of its fragment and vertex
shader (per compute event, of its kernel) on every core, or the reason there is none. loops_of() adds the prices of
the dynamic loops (core/loops.py) of the files whose longest path is N/A.
"""
import os

from paretogpu.adapters.unity import variants as unity_variants
from paretogpu.app.engine import Engine
from paretogpu.app.rules import CompileRule, LoopRule, MeasureRule
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.model.frame import Event
from paretogpu.model.variant import VariantKey, VariantState
from paretogpu.store import variant_store as store
from paretogpu.views.reporter import CONSOLE

NOT_COMPILED_YET = "not compiled yet (--no-compile)"


def shader_query(project, entry, shaders, root):
    return unity_variants.shader_query(project, entry, shaders, store.raw_dir(root))


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
    rep.log(f"pricing dynamic loops of {len(dyn)} files at n = {', '.join(map(str, loops.NS))} ...")
    rule = LoopRule(root)
    rule.force_files(dyn)
    res = Engine(rep, jobs).get(rule, [(fn, c) for fn in dyn for c in cores])
    out = {fn: {} for fn in dyn}
    for (fn, core), p in res.items():
        if p:
            out[fn][core] = p
    bad = [fn for fn in dyn if len(out[fn]) < len(cores)]
    if bad:
        rep.log(f"  loops not forced in {len(bad)} files (priced by total): " + ", ".join(bad[:5])
                + (" ..." if len(bad) > 5 else ""))
    return out
