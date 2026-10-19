"""Phone snapshot priced by the measured shader cost (core/frame_cost.measured)."""
import unittest

from paretogpu.core import frame_cost

G78 = "Mali-G78"


def row(i, pixels, vertices, stage="opaque", fragment=0.5, vertex=0.5):
    return {"index": i, "kind": "draw", "stage": stage, "object": f"o{i}", "shader": "S", "pass": "P", "rt": "rt",
            "variant": "v", "pixels": pixels, "vertices": vertices, "threads": 0, "loop": None,
            "cost": {G78: {"total": fragment + vertex, "fragment": fragment, "vertex": vertex, "compute": 0.0, "flags": ["f"],
                           "px_regs": 40}, "Mali-G52": {"total": 2.0}}}


class Measured(unittest.TestCase):
    def setUp(self):
        self.c = {"events": [row(0, 300, 30), row(1, 100, 10), {**row(2, 0, 0, "tile"), "kind": "tile"},
                             row(4, 50, 500, fragment=7.0, vertex=3.0)],
                  "geometry": [{**row(3, 0, 60, "shadow", fragment=0.0), "geometry": True}],
                  "cores": [{"name": G78}, {"name": "Mali-G52"}], "missing": []}
        self.events = [{"index": i, "rd": {"events": [100 + i]}} for i in (0, 1, 3, 4)]
        self.sc = {"error": None, "source": "streamline", "shaders": [
            {"mode": "colour", "events": [100, 101], "pipes": {"A": 4e6, "T": 1e6}},
            {"mode": "draw", "events": [100, 101], "pipes": {"A": 6e6}},
            {"mode": "draw", "events": [103], "pipes": {"LS": 2e6}},
            {"mode": "colour", "events": [104], "pipes": {"A": -1e6, "T": -2e5}},
            {"mode": "colour", "events": [999], "pipes": {"A": 9e6}, "error": "x"}]}

    def test_shaders_split_over_their_draws(self):
        c = self.c
        self.assertTrue(frame_cost.measured(c, self.sc, self.events, G78))
        got = {r["index"]: r["cost"][G78] for r in c["events"]}
        self.assertEqual(sorted(got), [0, 1, 2, 4])
        self.assertAlmostEqual(got[0]["fragment"], 4.5e6)  # pipe sum by pixels + its draw's rest (fullscreen)
        self.assertAlmostEqual(got[0]["px_price"], 1.5e4)
        self.assertAlmostEqual(got[0]["vertex"], 0.0)
        self.assertEqual(got[0]["px_bound"], ["arith"])
        self.assertEqual((got[0]["flags"], got[0]["px_regs"], got[0]["unmeasured"]), (["f"], 40, {}))
        self.assertEqual([r["index"] for r in c["geometry"]], [3])  # geometry stays apart, measured
        self.assertAlmostEqual(c["geometry"][0]["cost"][G78]["vertex"], 2e6)
        self.assertEqual([x["name"] for x in c["cores"]], [G78])
        self.assertAlmostEqual(c["totals"][G78]["total"], 6e6 + 1.0 + 10.0)  # + the tile row + the noisy draw's model

    def test_noise_keeps_the_model(self):
        frame_cost.measured(self.c, self.sc, self.events, G78)
        r = next(r["cost"][G78] for r in self.c["events"] if r["index"] == 4)
        self.assertEqual((r["fragment"], r["vertex"]), (7.0, 3.0))
        self.assertEqual(r["unmeasured"], {"fragment": "noise", "vertex": "not_swapped"})
        self.assertEqual(self.c["measured_fallback"], {"events": 1, "cycles": 10.0})

    def test_nothing_measured(self):
        self.assertFalse(frame_cost.measured({}, {"error": "hung", "shaders": []}, [], G78))
        self.assertFalse(frame_cost.measured({}, None, [], G78))


if __name__ == "__main__":
    unittest.main()
