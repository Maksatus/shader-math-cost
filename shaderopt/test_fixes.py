"""Regression tests for the fixes after the 2026-10-06 review: RenderDoc counters and call matching, malioc errors
and non-ASCII sources, splitting of "Compile and show code" output, the variant report. Synthetic data only.

Run: python -m unittest shaderopt.test_fixes   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from shaderopt import mali
from shaderopt.corpus import split_unity
from shaderopt.frame import renderdoc
from shaderopt.profile import cores, measure
from shaderopt.profile import report

SYNTH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus", "synthetic")


def draw(index, path, indices, calls=1):
    return {"index": index, "kind": "draw", "path": path, "draw_calls": calls, "indices": indices}


def action(event, path, indices, ps=None, instances=1):
    a = {"event": event, "path": path, "kinds": ["draw"], "indices": indices, "instances": instances}
    if ps is not None:
        a["ps_invocations"] = ps
    return a


class RenderDocMatchTest(unittest.TestCase):
    def test_missing_counter_is_none_not_zero(self):
        evs = [draw(0, "Cam/Opaque", 6)]
        acts = [action(10, "Cam/Opaque", 6, ps=100)]
        renderdoc.match(evs, acts, ["ps_invocations"])
        rd = evs[0]["rd"]
        self.assertEqual(rd["ps_invocations"], 100)
        self.assertIsNone(rd["vs_invocations"])
        self.assertIsNone(rd["cs_invocations"])

    def test_without_counter_list_old_behaviour(self):
        evs = [draw(0, "Cam/Opaque", 6)]
        renderdoc.match(evs, [action(10, "Cam/Opaque", 6, ps=100)])
        self.assertEqual(evs[0]["rd"]["vs_invocations"], 0)

    def test_extra_call_is_reported(self):
        # RenderDoc has one more draw on the path: the order shifts and the index counts disagree
        evs = [draw(0, "Cam/Opaque", 6), draw(1, "Cam/Opaque", 36)]
        acts = [action(10, "Cam/Opaque", 3), action(11, "Cam/Opaque", 6), action(12, "Cam/Opaque", 36)]
        matched, missing, mismatched = renderdoc.match(evs, acts, ["ps_invocations"])
        self.assertEqual((matched, missing, mismatched), (2, 0, 2))
        self.assertEqual(evs[0]["rd"]["count_mismatch"], [6, 3])

    def test_instances_and_batches_add_up(self):
        evs = [draw(0, "Cam/Opaque", 30, calls=2)]
        acts = [action(10, "Cam/Opaque", 6, instances=3), action(11, "Cam/Opaque", 12, instances=0)]
        self.assertEqual(renderdoc.match(evs, acts, []), (1, 0, 0))


class MaliocTest(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(mali.MALIOC), "malioc not installed")
    def test_non_ascii_source(self):
        with open(os.path.join(SYNTH, "sin_x4.frag"), encoding="utf-8") as f:
            src = f.read().replace("void main", "// комментарий \u2713 \ufffd\nvoid main")
        r = mali.measure(src, "Mali-G78", "gles", "fragment", cache=tempfile.mkdtemp())
        self.assertTrue(r["ok"], r.get("error"))

    def test_missing_malioc_is_a_failure_not_a_crash(self):
        with self.assertRaises(mali.MaliocError):
            mali.run_malioc("void main(){}", "Mali-G78", "gles", malioc=r"C:\nope\malioc.exe")
        with mock.patch.object(mali, "measure", side_effect=mali.MaliocError("not found")):
            r = measure.measure_one(os.path.join(SYNTH, "sin_x4.frag"), "Mali-G78", "gles")
        self.assertEqual(r, {"ok": False, "error": "not found"})

    def test_empty_core_list(self):
        with self.assertRaises(ValueError):
            cores.parse(" , ")


COMPILED = """Shader "Test/Lit" {
SubShader {
 Pass {
  Name "Forward"
//////////////////////////////////////////////////////
Keywords: _A
-- Vertex shader for "gles3":
// Compile errors generating this shader.

//////////////////////////////////////////////////////
Keywords: <none>
-- Hardware tier variant: Tier 1
-- Vertex shader for "gles3":
Shader Disassembly:
#ifdef VERTEX
#version 300 es
void main(){ gl_Position = vec4(0.0); }
#endif
#ifdef FRAGMENT
#version 300 es
precision mediump float;
layout(location = 0) out vec4 o;
void main(){ o = vec4(1.0); }
#endif
 }
}
}
"""


class SplitTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "Compiled-Test.shader")
        with open(self.src, "w", encoding="utf-8") as f:
            f.write(COMPILED)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_compile_errors_are_reported(self):
        shader, outputs, errors = split_unity.split(self.src, os.path.join(self.dir, "out"))
        self.assertEqual(shader, "Test/Lit")
        self.assertEqual(len(errors), 1)
        self.assertIn("[_A] vertex gles3", errors[0])
        self.assertEqual(sorted(m["file"] for man in outputs.values() for m in man),
                         ["00_Forward__none__tier1.frag", "00_Forward__none__tier1.vert"])

    def test_stale_files_of_an_earlier_split_are_removed(self):
        out = os.path.join(self.dir, "out")
        _, outputs, _ = split_unity.split(self.src, out)
        d = next(iter(outputs))
        with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
            man = json.load(f)
        open(os.path.join(d, "old.frag"), "w").close()
        open(os.path.join(d, "mine.txt"), "w").close()  # not written by a split: kept
        with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(man + [dict(man[0], file="old.frag")], f)
        split_unity.split(self.src, out)
        self.assertEqual(sorted(os.listdir(d)), ["00_Forward__none__tier1.frag", "00_Forward__none__tier1.vert", "manifest.json",
                                                 "mine.txt"])


def rec(file, core, arch, cycles, api="gles", stage="fragment"):
    c = {"arith": cycles, "fma": cycles, "cvt": 0.0, "sfu": 0.0, "ls": 0.0, "v": 0.0, "t": 0.0}
    v = {"longest": c, "shortest": c, "total": c, "bound": ["fma"], "work_regs": 8, "spilling": False,
         "fp16_pct": 50, "occupancy": 100}
    return {"file": file, "core": core, "arch": arch, "api": api, "stage": stage, "malioc": "x", "driver": "x",
            "variants": {"main": v}, "uniform_computation": False, "root": "D:/shaders"}


class ReportTest(unittest.TestCase):
    def test_rank_by_main_core_and_per_api(self):
        recs = [rec("a.frag", "Mali-G52", "Bifrost", 100), rec("a.frag", "Mali-G78", "Valhall", 1),
                rec("b.frag", "Mali-G52", "Bifrost", 10), rec("b.frag", "Mali-G78", "Valhall", 5),
                rec("c.frag.spv", "Mali-G78", "Valhall", 3, api="vulkan")]
        rows, _, _ = report.build_rows(recs)
        self.assertEqual([(r["file"], r["main"]["core"], r["rank"]) for r in rows],
                         [("b.frag", "Mali-G78", 1), ("c.frag.spv", "Mali-G78", 1), ("a.frag", "Mali-G78", 2)])



if __name__ == "__main__":
    unittest.main()
