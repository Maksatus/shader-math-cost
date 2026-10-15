import hashlib
import os
import threading

from paretogpu.adapters.gpu import backend, read_source
from paretogpu.adapters.unity import cli as unity
from paretogpu.adapters.unity import variants as unity_variants
from paretogpu.app.engine import MISSING, Rule
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.model.measurement import LoopProfile, Measurement
from paretogpu.model.shaderfile import stage_of
from paretogpu.model.variant import COMPUTE_NOT_COMPILED, NOT_FINISHED, OUTDATED, STOPPED, STOPPED_TWICE
from paretogpu.store import variant_store as store
from paretogpu.views.reporter import CONSOLE


def recover(root):
    """Places what a crashed / cut-off Unity run left in <root>/_compiled; -> {key: [errors]} or None."""
    raw = store.raw_dir(root)
    found = unity_variants.unfinished_run(raw)
    if not found:
        return None
    keys, result = found
    errors = store.place(root, raw, keys, result)
    unity_variants.mark_placed(raw)
    return errors


def compile_variants(project, keys, root, timeout=1800, rep=CONSOLE):
    """-> {key: [errors]}."""
    raw = store.raw_dir(root)
    result = unity_variants.compile_variants(project, keys, raw, timeout,
                                             on_start=lambda n: rep.phase("compile", n), on_step=rep.step)
    errors = store.place(root, raw, keys, result)
    unity_variants.mark_placed(raw)
    return errors


def current_fingerprints(project, shaders, root):
    return unity_variants.current_fingerprints(project, shaders, store.raw_dir(root))


class CompileRule(Rule):
    """VariantKey -> {"files": {stage: file}, "errors"}. Valid while all files exist and the shader's editor
    fingerprint (Unity version, platform defines, color space, dependency hash) matches; no editor -> used unchecked.
    Files of another fingerprint are never used: a failed recompile leaves no files, not the old code."""
    phase = "compile"
    keyed = False

    def __init__(self, project, root, compile_missing=True, recompile=False, retry_failed=False, rep=CONSOLE):
        self.project, self.root = project, root
        self.compile_missing, self.recompile, self.retry_failed = compile_missing, recompile, retry_failed
        self.rep = rep
        self.current = self.pipeline = None
        self.built = set()

    @property
    def checked(self):
        return self.current is not None

    def usable(self, k):
        """{} when the editor's fingerprint is known and the files were compiled with another one."""
        files = self.have.get(k, {})
        if self.current is None or not files or self.stored.get(k) == self.current.get(k.shader):
            return files
        return {}

    def _load(self):
        self.stored = {}
        self.have = store.load_manifests(self.root, self.stored)

    def complete(self, k):
        return all(s in self.have.get(k, {}) for s in k.stages)

    def errors_of(self, k):
        return [e for e in (self.index.get(k) or {}).get("errors", []) if e != NOT_FINISHED]

    def prepare(self, keys):
        self.keys = keys
        recovered = recover(self.root) or {}
        if recovered:
            n = sum(1 for e in recovered.values() if not e)
            self.rep.log(f"placed {n} variants of a Unity run that was not placed (it stopped or answered after the wait)")
        self._load()
        self.index = store.load_index(self.root)  # every variant this folder has seen, of any frame: {key: entry}
        draw = [k for k in keys if not k.is_compute]
        if self.compile_missing and draw:
            if self.project and unity.editor_ready(self.project, allow_play=True):
                self.rep.phase("fingerprints")
                answer = current_fingerprints(self.project, [k.shader for k in draw], self.root)
                self.current, self.pipeline = answer["shaders"], answer["pipeline"]
            else:
                self.rep.skip("fingerprints", "the editor does not answer")
                self.rep.log("the editor does not answer Unity CLI: compiled variants are used without checking "
                             "their shaders for changes")
        self.stale = [k for k in draw if self.complete(k) and self.current is not None
                      and (self.recompile or self.stored.get(k) is None
                           or self.stored.get(k) != self.current.get(k.shader))]

    def failed_before(self, k):
        """A failed variant is not recompiled until its shader changes (or retry_failed); unfinished run != failure."""
        if not self.errors_of(k) or self.retry_failed or self.recompile:
            return False
        return self.current is None or (self.index.get(k) or {}).get("fingerprint") == self.current.get(k.shader)

    def lookup(self, k):
        if k.is_compute:
            return {"files": self.have.get(k, {}),
                    "errors": self.errors_of(k) if self.have.get(k) else [COMPUTE_NOT_COMPILED]}
        if (not self.complete(k) or k in self.stale) and not self.failed_before(k):
            return MISSING
        return self._value(k, self.errors_of(k))

    def _value(self, k, errors):
        files = self.usable(k)
        if self.have.get(k) and not files and not errors:
            errors = [OUTDATED]
        return {"files": files, "errors": errors}

    def can_build(self):
        return self.compile_missing

    def skip_note(self):
        return "nothing to compile" if self.compile_missing else "--no-compile"

    def build(self, keys):
        if self.stale:
            self.rep.log(f"{len(self.stale)} compiled variants are out of date (shader, includes, Unity or platform "
                         f"defines changed{', --recompile' if self.recompile else ''}): compiling them again")
        self.rep.log(f"compiling {len(keys)} of {len(self.keys)} variants in Unity ...")
        errors = compile_variants(self.project, keys, self.root, rep=self.rep)
        self._load()
        self.built.update(keys)
        return {k: self._value(k, list(errors.get(k, []))) for k in keys}

    def unbuilt(self, k):
        """--no-compile: the reason is shown, not stored (the next run compiles it)."""
        return {"files": self.have.get(k, {}), "errors": self.errors_of(k), "pending": not self.complete(k)}

    def finish(self, values):
        for k, v in values.items():
            fp = (self.current.get(k.shader) if k in self.built and self.current is not None
                  else self.stored.get(k) or (self.current or {}).get(k.shader)
                  or (self.index.get(k) or {}).get("fingerprint"))
            old = self.index.get(k) or {}
            stops = (old.get("stopped") or 0) + 1 if STOPPED in v["errors"] else 0
            self.index[k] = {"key": k.to_list(),
                             "files": dict(sorted(self.have.get(k, {}).items())),
                             "errors": [e for e in v["errors"] if e not in (NOT_FINISHED, OUTDATED, STOPPED)]
                                       + ([STOPPED_TWICE] if stops >= 2 else []),
                             "fingerprint": fp, **({"stopped": stops} if stops else {})}
            v["files"] = self.usable(k)
        store.save_index(self.root, self.index)


class _Hashes:
    """sha1 per file, read once (a file is measured on several cores)."""

    def __init__(self, root):
        self.root, self.done, self.lock = root, {}, threading.Lock()

    def __call__(self, rel):
        with self.lock:
            h = self.done.get(rel)
        if h is None:
            with open(os.path.join(self.root, rel), "rb") as f:
                h = hashlib.sha1(f.read()).hexdigest()
            with self.lock:
                self.done[rel] = h
        return h


class MeasureRule(Rule):
    """(file relative to root, core) -> Measurement (Vulkan), stage from the extension; memo hits get "cached"."""
    phase = "measure"
    memo = "measure/1"

    def __init__(self, root, info=None, gpu=None):
        self.root, self.info = root, info or {}
        self.hash = _Hashes(root)
        self.gpu = gpu or backend()

    def memo_key(self, key):
        rel, core = key
        try:
            return f"{self.hash(rel)}|{core}|vulkan|{stage_of(rel)}|{self.gpu.tool}"
        except OSError:
            return None

    def remember(self, value):
        return bool(value.get("ok"))

    def _hit(self, rel, value):
        info = self.info.get(rel, {})
        return {"file": rel, "root": os.path.abspath(self.root),
                **{k: info[k] for k in ("shader", "pass", "keywords") if k in info},
                **{k: v for k, v in value.items() if k not in ("file", "root", "shader", "pass", "keywords")}}

    def build_one(self, key) -> Measurement:
        rel, core = key
        path = os.path.join(self.root, rel)
        try:
            r = self.gpu.measure(read_source(path), core, "vulkan", stage_of(path))
        except (OSError, self.gpu.Error) as e:  # fails this file, not the run
            r = {"ok": False, "error": str(e)}
        if not r["ok"]:
            return {"file": rel, "ok": False, "core": core, "api": "vulkan", "error": r["error"]}
        info = self.info.get(rel, {})
        return {"file": rel, "root": os.path.abspath(self.root),
                **{k: info[k] for k in ("shader", "pass", "keywords") if k in info}, **r}

    def from_memo(self, key, value):
        return {**self._hit(key[0], value), "cached": True}


def parametric(src, api, stage, core, gpu=None, forced=None) -> LoopProfile | None:
    """-> loops.profile() + "work_regs", "spilling" of src's forced loops on one core (forced: loops.forced(src) made
    already); None if not forceable, malioc failed or still N/A. A loop whose M-ladder does not grow evenly is measured
    again at half the iterations, down to 1."""
    forced = forced or loops.forced(src)
    if forced is None:
        return None
    plan, srcs = forced
    gpu = gpu or backend()
    regs, spill = 0, False

    def longest(f):
        r = gpu.measure(f, core, api, stage)
        return (heavy.combined(r, gpu.pipes)["longest"] if r["ok"] else None), r
    cs = []
    for (name, _, _, _), f in zip(plan, srcs):
        c, r = longest(f)
        if name in ("base", "zero"):  # the shader as it is: not unrolled to M iterations
            if c is None:
                return None
            regs = max(regs, max((v["work_regs"] or 0) for v in r["variants"].values()))
            spill = spill or gpu.spills(r)
        cs.append(c)
    prof = loops.profile(plan, cs)
    outer = loops.outer_loops(src)
    for k, lp in enumerate(prof["loops"]):
        g = loops.steps(lp)
        if loops.agrees(g) or (g and g[1] > g[0]):
            continue
        # later steps smaller or N/A: a constant cap below 4M (take a lower ladder that agrees and is steeper), or
        # malioc kept M's reads in registers (the lower ladders are flatter still: keep 2M..4M)
        m, low = lp["at"], None
        while m > 1 and low is None:
            m //= 2
            cand = {**lp, "at": m}
            for name, _, ns, nests in loops.ladder(outer, k, m):
                f = loops.force(src, ns, nests)
                cand[name] = longest(f)[0] if f is not None else None
            if loops.agrees(loops.steps(cand)):
                low = cand
        if low is not None and (g is None or g[1] <= 1e-6 or loops.steps(low)[0] > g[1]):
            prof["loops"][k] = low
        elif g is None or g[1] <= 1e-6:
            return None
    return {**prof, "work_regs": regs, "spilling": spill}


class LoopRule(Rule):
    """(file, core) -> LoopProfile; None when its loops cannot be forced (then priced by total cycles)."""
    phase = "loops"
    memo = "loops/4"

    def __init__(self, root, gpu=None):
        self.root = root
        self.gpu = gpu or backend()
        self.forced = {}
        self.locks = {}
        self.lock = threading.Lock()
        self.hash = _Hashes(root)

    def memo_key(self, key):
        fn, core = key
        try:
            return f"{self.hash(fn)}|{core}|{stage_of(fn)}|{loops.MODEL}|{self.gpu.tool}"
        except OSError:
            return None

    def _forced(self, fn):
        with self.lock:
            lock = self.locks.setdefault(fn, threading.Lock())
        with lock:
            if fn not in self.forced:
                src = read_source(os.path.join(self.root, fn))
                self.forced[fn] = src, loops.forced(src)
            return self.forced[fn]

    def build_one(self, key) -> LoopProfile | None:
        fn, core = key
        src, forced = self._forced(fn)
        return parametric(src, "vulkan", stage_of(fn), core, self.gpu, forced) if forced else None
