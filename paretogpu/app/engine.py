"""Incremental build of artifacts: what is still valid is taken, only the rest is built ("Build systems à la carte",
Mokhov, Mitchell, Peyton Jones).

A Rule is one kind of artifact. Two kinds of rules:
  keyed   every key is built on its own, in parallel, and is content-addressed: its key holds what it is made of
          (a shader file's text, a core), the cache sits in the adapter (adapters/malioc.py), so there is nothing to
          check: malioc runs, forced dynamic loops;
  batch   stored values are checked first (lookup: still valid for the current inputs?) and the missing ones are
          built in one go: the compiled variants (one Unity run compiles hundreds of them), whose inputs (the shader,
          its includes, Unity's defines) only the open editor can fingerprint.

  Engine(rep, jobs).get(rule, keys) -> {key: value}
"""
import concurrent.futures as cf
import os

from paretogpu.views.reporter import CONSOLE

MISSING = object()


class Rule:
    phase = None   # the progress phase its builds report as
    keyed = True

    def prepare(self, keys):
        """Before the lookups (batch rules): read the store, ask for the current inputs."""

    def lookup(self, key):
        """The stored value if it is still valid, else MISSING (batch rules)."""
        return MISSING

    def can_build(self):
        """False: the missing values are not built now (e.g. --no-compile), unbuilt() stands for them."""
        return True

    def skip_note(self):
        """Why the batch phase is skipped (nothing missing, or building not allowed)."""
        return None

    def build_one(self, key):
        """A keyed rule's value."""
        raise NotImplementedError

    def build(self, keys):
        """A batch rule's values of the missing keys: {key: value}."""
        raise NotImplementedError

    def unbuilt(self, key):
        """The value of a missing key that is not built now."""
        return None

    def finish(self, values):
        """After all keys have their value: write the store."""


class Engine:
    def __init__(self, rep=CONSOLE, jobs=None):
        self.rep = rep
        self.jobs = jobs or os.cpu_count()

    def get(self, rule, keys):
        keys = list(dict.fromkeys(keys))
        values = self._keyed(rule, keys) if rule.keyed else self._batch(rule, keys)
        rule.finish(values)
        return values

    def _keyed(self, rule, keys):
        if not keys:
            return {}
        self.rep.phase(rule.phase, len(keys))
        tick = self.rep.counter(len(keys))

        def one(k):
            v = rule.build_one(k)
            tick()
            return v

        with cf.ThreadPoolExecutor(self.jobs) as ex:
            return dict(zip(keys, ex.map(one, keys)))

    def _batch(self, rule, keys):
        rule.prepare(keys)
        values = {k: rule.lookup(k) for k in keys}
        missing = [k for k, v in values.items() if v is MISSING]
        if missing and rule.can_build():
            values.update(rule.build(missing))
        else:
            self.rep.skip(rule.phase, rule.skip_note())
            values.update({k: rule.unbuilt(k) for k in missing})
        return values
