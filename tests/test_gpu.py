"""adapters/gpu.py: the rules measure through a GPU backend; another backend plugs in without touching them.
A fake backend, a temporary shader file.

Run: python -m unittest discover tests   (from the repository root)
"""
import os
import shutil
import tempfile
import unittest

from paretogpu.adapters import gpu
from paretogpu.app.rules import MeasureRule
from paretogpu.model.errors import ParetoError
from paretogpu.model.measurement import MALI


class Fake(gpu.Backend):
    name = "fake"
    pipes = MALI
    tool = "fake-1"

    def measure(self, src, core, api, stage="fragment"):
        return {"ok": True, "core": core, "api": api, "stage": stage, "malioc": "fake", "variants": {}}


class GpuTest(unittest.TestCase):
    def test_default_and_unknown(self):
        self.assertEqual(gpu.backend().name, "mali")
        self.assertEqual(gpu.backend("mali").pipes, MALI)
        with self.assertRaises(ParetoError):
            gpu.backend("nothing")

    def test_another_backend(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "a.frag"), "w") as f:
                f.write("void main(){}")
            gpu.register(Fake())
            rule = MeasureRule(d, gpu=gpu.backend("fake"))
            r = rule.build_one(("a.frag", "Fake-1"))
            self.assertEqual((r["file"], r["core"], r["api"], r["stage"]), ("a.frag", "Fake-1", "gles", "fragment"))
            self.assertTrue(rule.memo_key(("a.frag", "Fake-1")).endswith("|fake-1"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
