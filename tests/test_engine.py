"""app/engine.py: batch rules take what is valid and build the rest in one go, keyed rules build every key in
parallel; app/variants.run() measures only the files of the requested variants. Synthetic data, stubs for malioc.

Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from paretogpu.adapters.gpu import backend
from paretogpu.app import variants
from paretogpu.app.engine import MISSING, Engine, Rule
from paretogpu.model.variant import VariantKey
from paretogpu.store.memo import Memo
from paretogpu.views.reporter import Reporter


class Recorder(Reporter):
    def __init__(self):
        super().__init__(machine=False, log=None)
        self.events = []

    def phase(self, pid, total=None, note=None):
        self.events.append(("phase", pid, total))

    def skip(self, pid, note=None):
        self.events.append(("skip", pid, note))


class Batch(Rule):
    phase = "make"
    keyed = False

    def __init__(self, stored, allowed=True):
        self.stored, self.allowed, self.built, self.finished = stored, allowed, [], None

    def lookup(self, key):
        return self.stored.get(key, MISSING)

    def can_build(self):
        return self.allowed

    def skip_note(self):
        return "nothing to do" if self.allowed else "not allowed"

    def build(self, keys):
        self.built.append(list(keys))
        return {k: k * 10 for k in keys}

    def unbuilt(self, key):
        return None

    def finish(self, values):
        self.finished = dict(values)


class Keyed(Rule):
    phase = "square"

    def build_one(self, key):
        return key * key


class Remembered(Keyed):
    memo = "square/1"

    def __init__(self):
        self.built = []

    def memo_key(self, key):
        return str(key)

    def build_one(self, key):
        self.built.append(key)
        return None if key < 0 else key * key


class EngineTest(unittest.TestCase):
    def test_batch_builds_only_what_is_missing(self):
        rep = Recorder()
        rule = Batch({1: "a", 3: "c"})
        self.assertEqual(Engine(rep).get(rule, [1, 2, 3, 2, 4]), {1: "a", 2: 20, 3: "c", 4: 40})
        self.assertEqual(rule.built, [[2, 4]])
        self.assertEqual(rule.finished, {1: "a", 2: 20, 3: "c", 4: 40})
        self.assertEqual(rep.events, [])

    def test_batch_skips_when_nothing_is_missing_or_building_is_off(self):
        rep = Recorder()
        self.assertEqual(Engine(rep).get(Batch({1: "a"}), [1]), {1: "a"})
        rule = Batch({}, allowed=False)
        self.assertEqual(Engine(rep).get(rule, [5]), {5: None})
        self.assertEqual(rule.built, [])
        self.assertEqual(rep.events, [("skip", "make", "nothing to do"), ("skip", "make", "not allowed")])

    def test_keyed_builds_every_key_in_parallel(self):
        rep = Recorder()
        self.assertEqual(Engine(rep, jobs=4).get(Keyed(), [3, 1, 3, 2]), {3: 9, 1: 1, 2: 4})
        self.assertEqual(rep.events, [("phase", "square", 3)])
        self.assertEqual(Engine(rep).get(Keyed(), []), {})

    def test_keyed_values_are_remembered(self):
        d = tempfile.mkdtemp()
        memo = Memo(os.path.join(d, "memo.sqlite"))
        try:
            first, second = Remembered(), Remembered()
            self.assertEqual(Engine(Recorder(), memo=memo).get(first, [2, 3, -1]), {2: 4, 3: 9, -1: None})
            self.assertEqual(Engine(Recorder(), memo=memo).get(second, [2, 3, -1, 5]), {2: 4, 3: 9, -1: None, 5: 25})
            self.assertEqual(sorted(first.built), [-1, 2, 3])
            self.assertEqual(sorted(second.built), [-1, 5])
        finally:
            memo.close()
            shutil.rmtree(d, ignore_errors=True)


class MeasureOnlyRequestedTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.key = VariantKey("Test/Lit", 0, 0, "Forward", ("_A",))
        other = VariantKey("Test/Other", 0, 0, "Forward", ())
        for k in (self.key, other):
            d = os.path.join(self.root, k.folder("vulkan"))
            os.makedirs(d, exist_ok=True)
            entries = []
            for s, stage in (("vert", "vertex"), ("frag", "fragment")):
                fn = k.base_name + (".vert.spv" if s == "vert" else ".frag.spv")
                with open(os.path.join(d, fn), "wb") as f:
                    f.write(b"spv")
                entries.append({"file": fn, "shader": k.shader, "pass": k.pass_name, "subshader": k.subshader,
                                "pass_index": k.pass_index, "keywords": list(k.keywords), "stage": stage,
                                "platform": "vulkan", "fingerprint": "fp"})
            with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(entries, f)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_only_the_variants_files_are_measured(self):
        measured = []

        def stub(src, core, api, stage, **kw):
            measured.append((stage, core))
            return {"ok": False, "error": "stub"}
        ev = {"index": 0, "kind": "draw", "shader": "Test/Lit", "subshader": 0, "pass_index": 0, "pass": "Forward",
              "keywords": ["_A"]}
        with mock.patch.object(backend(), "measure", side_effect=stub):
            st = variants.run([ev], None, self.root, ["Mali-G78", "Mali-G52"], ["vulkan"], compile_missing=False,
                              rep=Recorder())
        self.assertEqual(sorted(measured), [("fragment", "Mali-G52"), ("fragment", "Mali-G78"),
                                            ("vertex", "Mali-G52"), ("vertex", "Mali-G78")])
        self.assertEqual(set(st["measurements"]), set(st["files"][self.key].values()))
        self.assertFalse(st["checked"])


if __name__ == "__main__":
    unittest.main()
