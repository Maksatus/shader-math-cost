"""score.bottleneck() / score.score() on saved malioc 2026.5 JSON (mali/testdata, sources in corpus/synthetic).

Run: python -m unittest shaderopt.profile.test_score   (from the repository root)
"""
import json
import os
import unittest

from shaderopt import mali
from shaderopt.profile import score

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mali", "testdata")


def load(name):
    with open(os.path.join(DATA, name + ".json"), encoding="utf-8") as f:
        return mali.parse(json.load(f))


class BottleneckTest(unittest.TestCase):
    def test_valhall_g78_names_the_sub_pipes(self):
        # A = max(FMA, CVT, SFU) on G57..G78: the bound is named by the busiest sub-pipes
        cyc, bound = score.bottleneck(load("frag_g78_gles_sin_x4")["variants"]["main"]["longest"])
        self.assertEqual((cyc, bound), (1.0, ["fma", "sfu"]))

    def test_g710_arith_above_every_sub_pipe(self):
        # G710: FMA and SFU share issue, A = 10 > FMA 8 = SFU 8 (sin4_x16)
        c = load("frag_g710_gles_sin4_x16")["variants"]["main"]["longest"]
        self.assertEqual((c["arith"], c["fma"], c["sfu"]), (10.0, 8.0, 8.0))
        self.assertEqual(score.bottleneck(c), (10.0, ["arith"]))

    def test_bifrost_arith_only(self):
        cyc, bound = score.bottleneck(load("frag_g52_vulkan_branch")["variants"]["main"]["longest"])
        self.assertAlmostEqual(cyc, 3.9167, places=4)
        self.assertEqual(bound, ["arith"])

    def test_without_arith_the_busiest_sub_pipe(self):
        self.assertEqual(score.bottleneck({"fma": 2.0, "cvt": 1.0, "sfu": 3.0, "ls": 1.0}), (3.0, ["sfu"]))

    def test_non_arith_bound(self):
        self.assertEqual(score.bottleneck({"arith": 1.0, "fma": 1.0, "ls": 4.0, "t": 4.0}), (4.0, ["ls", "t"]))


class ScoreTest(unittest.TestCase):
    def test_dynamic_loop_falls_back_to_total_raised_to_shortest(self):
        rec = load("frag_g78_gles_loop_dynamic")
        v = rec["variants"]["main"]
        self.assertIsNone(v["longest"])
        s = score.score(rec)
        self.assertEqual(s["path"], "total")
        self.assertIn("dynamic_loop", s["flags"])
        c = score.fallback({"total": v["total"], "shortest": v["shortest"]})
        self.assertEqual(s["cycles"], round(score.bottleneck(c)[0], 4))
        # a pipe where the shortest path is above the total (spill traffic counted once in total) is raised
        self.assertEqual(score.fallback({"total": {"ls": 11.4, "fma": 3.0}, "shortest": {"ls": 182.0, "fma": 1.0}}),
                         {"ls": 182.0, "fma": 3.0})

    def test_price_from_elsewhere(self):
        rec = load("frag_g78_gles_loop_dynamic")
        s = score.score(rec, cycles={"arith": 7.0, "fma": 7.0, "ls": 2.0}, path="loop n=2")
        self.assertEqual((s["cycles"], s["bound"], s["path"]), (7.0, ["fma"], "loop n=2"))
        self.assertIn("dynamic_loop", s["flags"])


if __name__ == "__main__":
    unittest.main()
