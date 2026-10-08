"""Frame events -> measured shader variants (plan item K1.4).

Every draw of a Frame Debugger snapshot (frame_events.json) names its exact variant: shader, subshader,
pass index and the keywords it ran with. Those variants are compiled in the open editor
(adapters/unity/variants.py) into the variants folder of the project (store/variant_store.py) and measured with
malioc (features/measure.py) on the chosen cores. Variants already in the folder are not compiled again while they
are current: every compiled variant keeps the fingerprint of its shader (Unity version, Android platform defines,
color space, the asset's dependency hash: the file, its includes and subgraphs), and when the editor is open the
current fingerprints are asked from it and a variant whose shader changed is compiled again (--recompile: all of the
frame's variants). Without the editor (--no-compile) the files are used as they are and the result says they were
not checked. malioc results come from its cache. A failed variant is not compiled again until its shader changes
(or --retry-failed); a run that did not finish is not a failure.

Compute dispatches are keyed per kernel but not compiled (COMPUTE_NOT_COMPILED): they are listed without a price.
A Unity run that did not finish leaves its files in <variants>/_compiled; the next run places them (recover()).

run() -> the state core/variants.match() reads: per draw event, the measurement records of its fragment and vertex
shader (per compute event, of its kernel) on every core, or the reason there is none. loops_of() adds the prices of
the dynamic loops (core/loops.py) of the files whose longest path is N/A.
"""
import concurrent.futures as cf
import os

from paretogpu import progress as progress_ui
from paretogpu.adapters import malioc as mali
from paretogpu.adapters.unity import cli as unity
from paretogpu.adapters.unity import split as split_unity
from paretogpu.adapters.unity import variants as unity_variants
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.app import measure
from paretogpu.model.variant import COMPUTE, COMPUTE_NOT_COMPILED, NOT_FINISHED, key_of, stages_of
from paretogpu.store import variant_store as store


def _es31(platform, data):
    if platform != "gles3":
        return data, None
    text, patched = split_unity.es31(data.decode("utf-8", errors="replace"))
    return text.encode(), patched


def recover(root):
    """A Unity run that did not finish (the editor crashed or the connection broke) leaves its compiled files in
    <root>/_compiled without variants_result.json: place what is there. Returns {key: [errors]} or None."""
    raw = store.raw_dir(root)
    found = unity_variants.unfinished_run(raw)
    if not found:
        return None
    keys, result = found
    errors = store.place(root, raw, keys, result, _es31)
    unity_variants.mark_recovered(raw, result)
    return errors


def compile_variants(project, keys, root, platforms, timeout=1800):
    """Compile `keys` in the open editor; writes the files and manifests; returns {key: [errors]}."""
    raw = store.raw_dir(root)
    result = unity_variants.compile_variants(project, keys, raw, platforms, timeout,
                                             on_start=lambda n: progress_ui.phase("compile", n),
                                             on_step=progress_ui.step)
    return store.place(root, raw, keys, result, _es31)


def current_fingerprints(project, shaders, root):
    return unity_variants.current_fingerprints(project, shaders, store.raw_dir(root))


def shader_query(project, entry, shaders, root):
    return unity_variants.shader_query(project, entry, shaders, store.raw_dir(root))


def run(events, project, root, cores, platforms=("gles3", "vulkan"), jobs=None, compile_missing=True, progress=print,
        recompile=False, retry_failed=False):
    """Compile and measure the variants of the frame's draws. Returns the state for core/variants.match():
    {"keys", "files": {key: {(platform, stage): file}}, "errors": {key: [..]}, "measurements",
     "checked": True if the compiled files were checked against the shaders in the open editor}.
    recompile: compile every variant of the frame again; retry_failed: also the ones that failed before."""
    root = os.path.abspath(root)
    project = project and os.path.abspath(project)
    keys = list(dict.fromkeys(key_of(e) for e in events if e["kind"] in ("draw", "compute")))
    recovered = recover(root) or {}
    if recovered:
        progress(f"recovered {sum(1 for e in recovered.values() if not e)} variants of an unfinished Unity run")
    stored = {}
    have = store.load_manifests(root, stored, platforms)
    index = store.load_index(root)  # every variant this folder has seen, of any frame: {key: entry}
    draw_keys = [k for k in keys if k[1] != COMPUTE]  # compute kernels are not compiled: see COMPUTE_NOT_COMPILED

    def complete(k):
        return all((p, s) in have.get(k, {}) for p in platforms for s in stages_of(k))

    current = None
    if compile_missing and draw_keys:
        if project and unity.editor_ready(project, allow_play=True):
            progress_ui.phase("fingerprints")
            current = current_fingerprints(project, [k[0] for k in draw_keys], root)
        else:
            progress_ui.skip("fingerprints", "the editor does not answer")
            progress("the editor does not answer Unity CLI: compiled variants are used without checking "
                     "their shaders for changes")

    def failed_before(k):
        """A variant that failed is not compiled again until its shader changes (or retry_failed)."""
        errs = [e for e in (index.get(k) or {}).get("errors", []) if e != NOT_FINISHED]
        if not errs or retry_failed or recompile:
            return False
        return current is None or (index.get(k) or {}).get("fingerprint") == current.get(k[0])

    stale = [k for k in draw_keys if complete(k) and current is not None
             and (recompile or stored.get(k) is None or stored.get(k) != current.get(k[0]))]
    missing = [k for k in draw_keys if (not complete(k) or k in stale) and not failed_before(k)]
    errors = {k: [e for e in (index.get(k) or {}).get("errors", []) if e != NOT_FINISHED] for k in keys}
    for k in keys:
        if k[1] == COMPUTE and not have.get(k):
            errors[k] = [COMPUTE_NOT_COMPILED]
    if missing and compile_missing:
        if stale:
            progress(f"{len(stale)} compiled variants are out of date (shader, includes, Unity or platform defines "
                     f"changed{', --recompile' if recompile else ''}): compiling them again")
        progress(f"compiling {len(missing)} of {len(keys)} variants in Unity ...")
        errors.update(compile_variants(project, missing, root, platforms))
        stored = {}
        have = store.load_manifests(root, stored, platforms)
    else:
        progress_ui.skip("compile", "nothing to compile" if compile_missing else "--no-compile")
    pending = set() if compile_missing else {k for k in missing if not complete(k)}
    if any(have.get(k) for k in keys):
        progress(f"measuring {root} on {', '.join(cores)} ...")
        records, failures = measure.run(root, cores, "gles", root, jobs)
        if failures:
            progress(f"  malioc failed on {len(failures)} files")
    for k in keys:
        index[k] = {"key": [k[0], k[1], k[2], k[3], list(k[4])],
                    "files": {f"{p}/{s}": fn for (p, s), fn in sorted(have.get(k, {}).items())},
                    "errors": [e for e in errors.get(k, []) if e != NOT_FINISHED],
                    "fingerprint": stored.get(k) or (current or {}).get(k[0])}
    store.save_index(root, index)
    # not compiled in this run: the reason is shown, not stored (the next run compiles them)
    for k in pending:
        errors[k] = errors[k] or ["not compiled yet (--no-compile)"]
    return {"keys": keys, "files": {k: have.get(k, {}) for k in keys}, "errors": errors,
            "measurements": store.load_measurements(root), "checked": current is not None}


def parametric(forced, api, stage, core, cache=mali.CACHE):
    """{"c": [cycles at n = 0, 1, 2] (combined variants, longest path), "work_regs", "spilling"} of the forced
    sources ([loops.force(src, n) for n in loops.NS]) on one core, or None (cannot be forced, malioc failed, still
    N/A)."""
    if any(f is None for f in forced):
        return None
    cs, regs, spill = [], 0, False
    for f in forced:
        r = mali.measure(f, core, api, stage, cache=cache)
        if not r["ok"]:
            return None
        c = heavy.combined(r)["longest"]
        if c is None:
            return None
        cs.append(c)
        regs = max(regs, max((v["work_regs"] or 0) for v in r["variants"].values()))
        spill = spill or mali.spills(r)
    return {"c": cs, "work_regs": regs, "spilling": spill}


def loops_of(state, root, cores, jobs=None, progress=print):
    """Loop prices of every measured file of the variants whose longest path is N/A on some core:
    {relative file: {core: parametric()}}; files whose loops cannot be forced map to {}."""
    files = sorted({fn for k in state["keys"] for fn in state["files"].get(k, {}).values()})
    dyn = [fn for fn in files
           if any(r.get("ok", True) and heavy.combined(r)["longest"] is None
                  for r in state["measurements"].get(fn, {}).values())]
    if not dyn:
        progress_ui.skip("loops")
        return {}
    progress(f"pricing dynamic loops of {len(dyn)} files at n = {', '.join(map(str, loops.NS))} ...")
    progress_ui.phase("loops", len(dyn) * len(cores))
    forced = {}
    for fn in dyn:
        src = mali.read_source(os.path.join(root, fn))
        forced[fn] = [loops.force(src, n) for n in loops.NS]
    tasks = [(fn, c) for fn in dyn for c in cores]
    tick = progress_ui.counter(len(tasks))

    def job(t):
        fn, core = t
        api = "vulkan" if fn.endswith(mali.SPIRV_EXT) else "gles"
        r = parametric(forced[fn], api, mali.stage_of(fn), core)
        tick()
        return r

    with cf.ThreadPoolExecutor(jobs or os.cpu_count()) as ex:
        res = list(ex.map(job, tasks))
    out = {fn: {} for fn in dyn}
    for (fn, core), p in zip(tasks, res):
        if p:
            out[fn][core] = p
    bad = [fn for fn in dyn if len(out[fn]) < len(cores)]
    if bad:
        progress(f"  loops not forced in {len(bad)} files (priced by total): " + ", ".join(bad[:5])
                 + (" ..." if len(bad) > 5 else ""))
    return out
