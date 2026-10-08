"""Comparison of cost runs (core/compare.py, store/cost_runs.py) on synthetic runs: no project data.

Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import tempfile
import unittest

from paretogpu.core import compare
from paretogpu.store import cost_runs
from paretogpu.views import html

CORE = "Mali-G78"


def event(index, shader, pixels, px_price, vertices=0, vtx_price=0.0, keywords=(), obj="obj", flags=()):
    frag, vert = pixels * px_price, vertices * vtx_price
    return {"index": index, "kind": "draw", "stage": "opaque", "object": obj, "shader": shader, "pass": "P",
            "keywords": list(keywords), "rt": "Color", "rt_size": [100, 100], "pixels": pixels,
            "vertices": vertices, "threads": 0,
            "variant": f"{shader} | 0.0 P | {' '.join(keywords) or '-'}",
            "cost": {CORE: {"px_price": px_price, "vtx_price": vtx_price, "fragment": frag, "vertex": vert,
                            "compute": 0.0, "total": frag + vert, "flags": list(flags)}}}


def run(events, frame_dir="F", **kw):
    total = sum(e["cost"][CORE]["total"] for e in events)
    return {"frame": {"project": "P", "quality": "Q"}, "api": "vulkan", "malioc": "1", "main_core": CORE,
            "cores": [{"name": CORE}], "totals": {CORE: {"total": total}}, "loops": {"n": 2},
            "frame_dir": frame_dir, "events": events, "missing": [], "variants_checked": True, **kw}


class Compare(unittest.TestCase):
    def test_same_snapshot_only_price(self):
        a = run([event(0, "Lit", 1000, 10.0, 300, 4.0), event(1, "Lit", 500, 10.0, 300, 4.0, obj="o2")])
        b = run([event(0, "Lit", 1000, 8.0, 300, 4.0), event(1, "Lit", 500, 8.0, 300, 4.0, obj="o2")])
        c = compare.compare(a, b)
        self.assertTrue(c["same_frame"])
        x = c["by_core"][CORE]
        self.assertAlmostEqual(x["delta"], -3000.0)
        self.assertAlmostEqual(x["price"], -3000.0)
        self.assertAlmostEqual(x["work"], 0.0)
        self.assertEqual(x["mix"], 0.0)

    def test_split_is_exact(self):
        """price + work + mix = B - A for every shader and the frame, with work and price both changing."""
        a = run([event(0, "Lit", 1000, 10.0, 200, 3.0), event(1, "Fog", 4000, 2.0), event(2, "Old", 100, 5.0)])
        b = run([event(0, "Lit", 1500, 7.0, 260, 3.5), event(1, "Fog", 4000, 2.0, keywords=["_HQ"]),
                 event(2, "New", 300, 1.0)], frame_dir="G")
        c = compare.compare(a, b)
        self.assertFalse(c["same_frame"])
        x = c["by_core"][CORE]
        self.assertAlmostEqual(x["price"] + x["work"] + x["mix"], x["delta"])
        for s in x["shaders"]:
            self.assertAlmostEqual(s["price"] + s["work"] + s["mix"], s["delta"], msg=s["shader"])
        by = {s["shader"]: s for s in x["shaders"]}
        lit = by["Lit"]
        # fragment: price (7 - 10) * (1000 + 1500) / 2, work (1500 - 1000) * (10 + 7) / 2; vertex likewise
        self.assertAlmostEqual(lit["price"], -3750.0 + 0.5 * 230)
        self.assertAlmostEqual(lit["work"], 4250.0 + 60 * 3.25)
        self.assertEqual(by["Old"]["status"], "gone")
        self.assertEqual(by["New"]["status"], "new")
        self.assertEqual(by["Fog"]["status"], "mixed")  # the same shader with other keywords: a new variant
        self.assertAlmostEqual(by["Fog"]["delta"], 0.0)

    def test_flags_and_warnings(self):
        a = run([event(0, "Lit", 1000, 10.0, flags=["spilling"])])
        b = run([event(0, "Lit", 1000, 9.0, flags=["low_fp16"])], malioc="2", api="gles")
        c = compare.compare(a, b)
        s = c["by_core"][CORE]["shaders"][0]
        self.assertEqual(s["flags_added"], ["low_fp16"])
        self.assertEqual(s["flags_removed"], ["spilling"])
        self.assertTrue(any("malioc" in w for w in c["warnings"]))
        self.assertTrue(any("API" in w for w in c["warnings"]))

    def test_events_matched_by_object(self):
        a = run([event(0, "Lit", 100, 1.0, obj="x"), event(1, "Lit", 200, 1.0, obj="y")])
        b = run([event(5, "Lit", 200, 1.0, obj="y"), event(6, "Lit", 400, 1.0, obj="x")])
        v = compare.compare(a, b)["by_core"][CORE]["shaders"][0]["variants"][0]
        x = next(e for e in v["events"] if e["object"] == "x")
        self.assertEqual((x["index_a"], x["index_b"], x["delta"]), (0, 6, 300.0))

    def test_archive_and_render(self):
        with tempfile.TemporaryDirectory() as d:
            c = run([event(0, "Lit", 1000, 10.0)], computed_at=1.0)
            with open(os.path.join(d, "frame_cost.json"), "w", encoding="utf-8") as f:
                json.dump(c, f)
            r1 = cost_runs.archive(c, d)
            r2 = cost_runs.archive(c, d)
            self.assertNotEqual(r1, r2)
            self.assertEqual([m["id"] for m in cost_runs.runs(d)], [r1, r2])
            page = html.render_result(compare.compare(cost_runs.load(cost_runs.run_path(d, r1)), cost_runs.load(d)))
            self.assertNotIn("/*COMPARE_DATA*/null", page)
            self.assertIn('"same_frame": true', page)


if __name__ == "__main__":
    unittest.main()
