"""Incremental build. Keyed rules: each key built alone, in parallel, content-addressed (memo_key must hold everything
the value depends on; a memo hit is never re-checked). Batch rules: lookup() validates stored values, the missing ones
are built in one go (one Unity run compiles hundreds of variants)."""
import concurrent.futures as cf
import os
import threading

from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

MISSING = object()


class Rule:
    phase = None
    keyed = True
    memo = None    # keyed rules: memo kind with version ("loops/1"), or None

    def memo_key(self, key):
        """None: not remembered."""
        return None

    def remember(self, value):
        """False for failures that may go away."""
        return value is not None

    def from_memo(self, key, value):
        """A rule may add here what depends on the key, not the content."""
        return value

    def prepare(self, keys):
        """Batch rules: read the store and the current inputs before lookups."""

    def lookup(self, key):
        """Batch rules: the stored value if still valid, else MISSING."""
        return MISSING

    def can_build(self):
        """False (e.g. --no-compile): unbuilt() stands for the missing values."""
        return True

    def skip_note(self):
        return None

    def build_one(self, key):
        raise NotImplementedError

    def build(self, keys):
        raise NotImplementedError

    def unbuilt(self, key):
        return None

    def finish(self, values):
        """Called with all values: write the store."""


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
