"""The artifacts of a frame's (or materials') variants, as rules of app/engine.py.

  CompileRule  VariantKey -> {"files": {(platform, stage): file}, "errors": [..]} in the variants folder
  MeasureRule  (file, core) -> malioc's Measurement of the file on the core
  LoopRule     (file, core) -> LoopProfile: the file's dynamic loops forced to n = 0, 1, 2 and measured
"""
import os

from paretogpu.adapters import malioc as mali
from paretogpu.adapters.unity import cli as unity
from paretogpu.adapters.unity import split as split_unity
from paretogpu.adapters.unity import variants as unity_variants
from paretogpu.app.engine import MISSING, Rule
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.model.measurement import LoopProfile, Measurement
from paretogpu.model.variant import COMPUTE_NOT_COMPILED, NOT_FINISHED
from paretogpu.store import variant_store as store
from paretogpu.views.reporter import CONSOLE


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


def compile_variants(project, keys, root, platforms, timeout=1800, rep=CONSOLE):
    """Compile `keys` in the open editor; writes the files and manifests; returns {key: [errors]}."""
    raw = store.raw_dir(root)
    result = unity_variants.compile_variants(project, keys, raw, platforms, timeout,
                                             on_start=lambda n: rep.phase("compile", n), on_step=rep.step)
    return store.place(root, raw, keys, result, _es31)


def current_fingerprints(project, shaders, root):
    return unity_variants.current_fingerprints(project, shaders, store.raw_dir(root))


class CompileRule(Rule):
    """Compiled variants. A stored variant is valid while all its files are there and its shader's fingerprint (Unity
    version, Android platform defines, color space, the asset's dependency hash: the file, its includes and subgraphs)
    is the one it was compiled with; the current fingerprints are asked from the open editor, without it the stored
    files are used unchecked (checked = False). A variant that failed is not compiled again until its shader changes
    (or retry_failed); a run that did not finish is not a failure. recompile: every requested variant again.
    Compute kernels are keyed but not compiled (COMPUTE_NOT_COMPILED)."""
    phase = "compile"
    keyed = False

    def __init__(self, project, root, platforms, compile_missing=True, recompile=False, retry_failed=False,
                 rep=CONSOLE):
        self.project, self.root, self.platforms = project, root, list(platforms)
        self.compile_missing, self.recompile, self.retry_failed = compile_missing, recompile, retry_failed
        self.rep = rep
        self.current = None

    @property
    def checked(self):
        return self.current is not None

    def _load(self):
        self.stored = {}
        self.have = store.load_manifests(self.root, self.stored, self.platforms)

    def complete(self, k):
        return all((p, s) in self.have.get(k, {}) for p in self.platforms for s in k.stages)

    def errors_of(self, k):
        return [e for e in (self.index.get(k) or {}).get("errors", []) if e != NOT_FINISHED]

    def prepare(self, keys):
        self.keys = keys
        recovered = recover(self.root) or {}
        if recovered:
            self.rep.log(f"recovered {sum(1 for e in recovered.values() if not e)} variants of an unfinished Unity run")
        self._load()
        self.index = store.load_index(self.root)  # every variant this folder has seen, of any frame: {key: entry}
        draw = [k for k in keys if not k.is_compute]  # compute kernels are not compiled: see COMPUTE_NOT_COMPILED
        if self.compile_missing and draw:
            if self.project and unity.editor_ready(self.project, allow_play=True):
                self.rep.phase("fingerprints")
                self.current = current_fingerprints(self.project, [k.shader for k in draw], self.root)
            else:
                self.rep.skip("fingerprints", "the editor does not answer")
                self.rep.log("the editor does not answer Unity CLI: compiled variants are used without checking "
                             "their shaders for changes")
        self.stale = [k for k in draw if self.complete(k) and self.current is not None
                      and (self.recompile or self.stored.get(k) is None
                           or self.stored.get(k) != self.current.get(k.shader))]

    def failed_before(self, k):
        """A variant that failed is not compiled again until its shader changes (or retry_failed)."""
        if not self.errors_of(k) or self.retry_failed or self.recompile:
            return False
        return self.current is None or (self.index.get(k) or {}).get("fingerprint") == self.current.get(k.shader)

    def lookup(self, k):
        if k.is_compute:
            return {"files": self.have.get(k, {}),
                    "errors": self.errors_of(k) if self.have.get(k) else [COMPUTE_NOT_COMPILED]}
        if (not self.complete(k) or k in self.stale) and not self.failed_before(k):
            return MISSING
        return {"files": self.have.get(k, {}), "errors": self.errors_of(k)}

    def can_build(self):
        return self.compile_missing

    def skip_note(self):
        return "nothing to compile" if self.compile_missing else "--no-compile"

    def build(self, keys):
        if self.stale:
            self.rep.log(f"{len(self.stale)} compiled variants are out of date (shader, includes, Unity or platform "
                         f"defines changed{', --recompile' if self.recompile else ''}): compiling them again")
        self.rep.log(f"compiling {len(keys)} of {len(self.keys)} variants in Unity ...")
        errors = compile_variants(self.project, keys, self.root, self.platforms, rep=self.rep)
        self._load()
        return {k: {"files": self.have.get(k, {}), "errors": list(errors.get(k, []))} for k in keys}

    def unbuilt(self, k):
        """Not compiled in this run (--no-compile): the reason is shown, not stored (the next run compiles it)."""
        return {"files": self.have.get(k, {}), "errors": self.errors_of(k), "pending": not self.complete(k)}

    def finish(self, values):
        for k, v in values.items():
            self.index[k] = {"key": k.to_list(),
                             "files": {f"{p}/{s}": fn for (p, s), fn in sorted(self.have.get(k, {}).items())},
                             "errors": [e for e in v["errors"] if e != NOT_FINISHED],
                             "fingerprint": self.stored.get(k) or (self.current or {}).get(k.shader)}
            v["files"] = self.have.get(k, {})
        store.save_index(self.root, self.index)


def api_of(path, api):
    return "vulkan" if path.endswith(mali.SPIRV_EXT) else api


class MeasureRule(Rule):
    """malioc on one file (relative to `root`) and one core: GLSL for `api`, SPIR-V always for Vulkan; the stage comes
    from the extension. The value keeps whether it came from malioc's cache ("cached")."""
    phase = "measure"

    def __init__(self, root, api="gles", info=None):
        self.root, self.api, self.info = root, api, info or {}

    def build_one(self, key) -> Measurement:
        rel, core = key
        path = os.path.join(self.root, rel)
        try:
            r = mali.measure(mali.read_source(path), core, api_of(path, self.api), mali.stage_of(path))
        except (OSError, mali.MaliocError) as e:  # fails this file, not the run
            r = {"ok": False, "error": str(e)}
        if not r["ok"]:
            return {"file": rel, "ok": False, "core": core, "api": api_of(path, self.api), "error": r["error"]}
        info = self.info.get(rel, {})
        return {"file": rel, "root": os.path.abspath(self.root),
                **{k: info[k] for k in ("shader", "pass", "keywords") if k in info}, **r}


def parametric(forced, api, stage, core, cache=mali.CACHE) -> LoopProfile | None:
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


class LoopRule(Rule):
    """The dynamic loops of a file forced to n = 0, 1, 2 iterations (core/loops.py) and measured on one core;
    None when its loops cannot be forced (it is priced by total cycles then)."""
    phase = "loops"

    def __init__(self, root):
        self.root = root
        self.forced = {}

    def build_one(self, key) -> LoopProfile | None:
        fn, core = key
        api = "vulkan" if fn.endswith(mali.SPIRV_EXT) else "gles"
        return parametric(self.forced[fn], api, mali.stage_of(fn), core)

    def force_files(self, files):
        for fn in files:
            src = mali.read_source(os.path.join(self.root, fn))
            self.forced[fn] = [loops.force(src, n) for n in loops.NS]
