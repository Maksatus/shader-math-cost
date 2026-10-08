"""parse() on saved malioc 2026.5 JSON (testdata/, sources in corpus/synthetic).

Expected values are copied from the text output of malioc for the same shaders.
Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import unittest

from paretogpu.adapters import malioc as mali

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "malioc")


def load(name):
    with open(os.path.join(DATA, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


class ParseTest(unittest.TestCase):
    def test_fragment_valhall(self):
        r = mali.parse(load("frag_g78_gles_sin_x4"))
        self.assertEqual((r["core"], r["arch"], r["api"], r["stage"]), ("Mali-G78", "Valhall", "gles", "fragment"))
        self.assertEqual((r["driver"], r["malioc"]), ("r51p0-00rel0", "2026.5.0"))
        self.assertIs(r["uniform_computation"], False)
        self.assertEqual(list(r["variants"]), ["main"])
        v = r["variants"]["main"]
        self.assertEqual(v["longest"], {"arith": 1.0, "fma": 1.0, "cvt": 0.09375, "sfu": 1.0,
                                        "ls": 0.0, "v": 0.625, "t": 0.0})
        self.assertEqual(v["bound"], ["arith", "fma", "sfu"])
        self.assertEqual((v["work_regs"], v["uniform_regs"], v["occupancy"]), (14, 4, 100))
        self.assertEqual((v["spilling"], v["spill_bytes"], v["fp16_pct"]), (False, 0, 0))

    def test_vertex_idvs(self):
        r = mali.parse(load("vert_g78_gles_wave_pos"))
        self.assertEqual(r["stage"], "vertex")
        self.assertIs(r["uniform_computation"], True)
        self.assertEqual(list(r["variants"]), ["position", "varying"])
        pos, var = r["variants"]["position"], r["variants"]["varying"]
        self.assertAlmostEqual(pos["longest"]["fma"], 3.4, places=5)
        self.assertEqual((pos["longest"]["sfu"], pos["longest"]["ls"]), (2.125, 3.0))
        self.assertNotIn("v", pos["longest"])  # vertex shaders have no varying pipe
        self.assertEqual(pos["bound"], ["arith", "fma"])
        self.assertEqual(pos["work_regs"], 32)
        self.assertEqual((var["longest"]["fma"], var["longest"]["ls"]), (0.0, 5.0))
        self.assertEqual(var["bound"], ["ls"])
        self.assertEqual(var["work_regs"], 11)

    def test_bifrost_single_arith_pipe_and_branch(self):
        r = mali.parse(load("frag_g52_vulkan_branch"))
        self.assertEqual((r["arch"], r["api"]), ("Bifrost", "vulkan"))
        v = r["variants"]["main"]
        self.assertEqual(set(v["longest"]), {"arith", "ls", "v", "t"})
        self.assertAlmostEqual(v["longest"]["arith"], 3.9167, places=4)
        self.assertAlmostEqual(v["shortest"]["arith"], 0.1667, places=4)
        self.assertEqual(v["shortest"]["v"], 0.25)
        self.assertEqual((v["bound"], v["work_regs"]), (["arith"], 22))

    def test_spilling_5th_gen(self):
        r = mali.parse(load("frag_g720_gles_spill"))
        self.assertEqual((r["core"], r["arch"], r["driver"]), ("Immortalis-G720", "Arm 5th Generation", "r56p1-00rel0"))
        v = r["variants"]["main"]
        self.assertEqual((v["spilling"], v["spill_bytes"], v["occupancy"]), (True, 352, 50))
        self.assertEqual((v["work_regs"], v["uniform_regs"]), (64, 32))
        self.assertEqual((v["longest"]["ls"], v["longest"]["arith"]), (179.0, 16.0))
        self.assertAlmostEqual(v["total"]["ls"], 11.2, places=5)
        self.assertEqual(v["bound"], ["ls"])
        self.assertTrue(mali.spills(r))

    def test_longest_na_dynamic_loop(self):
        # malioc text output: "Longest path cycles: N/A", total and shortest are present
        v = mali.parse(load("frag_g78_gles_loop_dynamic"))["variants"]["main"]
        self.assertIsNone(v["longest"])
        self.assertEqual(v["bound"], [])
        self.assertEqual((v["total"]["fma"], v["total"]["sfu"]), (1.0, 1.0))
        self.assertEqual(v["shortest"]["cvt"], 0.1875)
        self.assertEqual(v["work_regs"], 26)

    def test_legacy_fields(self):
        # the cache entry fields bench/run.py reads, as the old run.py computed them
        leg = mali._legacy(load("frag_g78_gles_sin_x4"))
        self.assertEqual(leg["cycles"]["arith_sfu"], 1.0)
        self.assertEqual(leg["short"]["varying"], 0.625)
        self.assertEqual(leg["bound"], ["arith_total", "arith_fma", "arith_sfu"])
        self.assertEqual(leg["props"]["work_registers_used"], 14)
        self.assertEqual(leg["driver"], "r51p0-00rel0")
        self.assertFalse(mali.spills(leg))


if __name__ == "__main__":
    unittest.main()
