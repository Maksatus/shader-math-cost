"""Incremental build of artifacts: what is still valid is taken, only the rest is built ("Build systems à la carte",
Mokhov, Mitchell, Peyton Jones).

A Rule is one kind of artifact. Two kinds of rules:
  keyed   every key is built on its own, in parallel, and is content-addressed: memo_key(key) holds what it is made
          of (the hash of a shader file's text, a core, malioc's version), so there is nothing to check: a value
          remembered under that key (store/memo.py, kind = the rule's memo name with its version) is the value;
          malioc runs, forced dynamic loops;
  batch   stored values are checked first (lookup: still valid for the current inputs?) and the missing ones are
          built in one go: the compiled variants (one Unity run compiles hundreds of them), whose inputs (the shader,
          its includes, Unity's defines) only the open editor can fingerprint.

  Engine(rep, jobs).get(rule, keys) -> {key: value}
  Engine(...).get(rule, keys, tick)  a keyed rule counting into a progress counter shared with other gets (no phase)
"""
import concurrent.futures as cf
import os
import threading

from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

MISSING = object()


class Rule:
    phase = None   # the progress phase its builds report as
    keyed = True
    memo = None    # keyed rules: the name and version their values are remembered under ("loops/1"), or None

    def memo_key(self, key):
        """What a keyed rule's value is made of, as a string (None: not remembered)."""
        return None

    def remember(self, value):
        """Whether a built value may be remembered (not a failure that may go away)."""
        return value is not None

    def from_memo(self, key, value):
        """The value of `key` from what was remembered (a rule may add what depends on the key, not the content)."""
        return value

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
    def __init__(self, rep=CONSOLE, jobs=None, memo=None):
        self.rep = rep
        self.jobs = jobs or os.cpu_count()
        self.memo = memo

    def get(self, rule, keys, tick=None):
        keys = list(dict.fromkeys(keys))
        values = self._keyed(rule, keys, tick) if rule.keyed else self._batch(rule, keys)
        rule.finish(values)
        return values

    def _keyed(self, rule, keys, tick=None):
        if not keys:
            return {}
        if tick is None:
            self.rep.phase(rule.phase, len(keys))
            tick = self.rep.counter(len(keys))
        memo = (self.memo or memo_store.default()) if rule.memo else None
        new, lock = [], threading.Lock()

        def one(k):
            mk = memo and rule.memo_key(k)
            v = memo.get(rule.memo, mk, MISSING) if mk else MISSING
            if v is not MISSING:
                v = rule.from_memo(k, v)
            else:
                v = rule.build_one(k)
                if mk and rule.remember(v):
                    with lock:
                        new.append((mk, v))
            tick()
            return v

        with cf.ThreadPoolExecutor(self.jobs) as ex:
            values = dict(zip(keys, ex.map(one, keys)))
        if memo:
            memo.put_many(rule.memo, new)
        return values

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
