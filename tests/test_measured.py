"""Phone snapshot priced by the measured shader cost (core/frame_cost.measured)."""
import unittest

from paretogpu.core import frame_cost

G78 = "Mali-G78"


def row(i, pixels, vertices, stage="opaque"):
    return {"index": i, "kind": "draw", "stage": stage, "object": f"o{i}", "shader": "S", "pass": "P", "rt": "rt",
            "variant": "v", "pixels": pixels, "vertices": vertices, "threads": 0, "loop": None,
            "cost": {G78: {"total": 1.0}, "Mali-G52": {"total": 2.0}}}


class Measured(unittest.TestCase):
    def test_shaders_split_over_their_draws(self):
        c = {"events": [row(0, 300, 30), row(1, 100, 10), {**row(2, 0, 0), "kind": "tile"}],
             "geometry": [{**row(3, 0, 60, "shadow"), "geometry": True}], "cores": [{"name": G78}, {"name": "Mali-G52"}]}
        events = [{"index": 0, "rd": {"events": [100]}}, {"index": 1, "rd": {"events": [101]}},
                  {"index": 3, "rd": {"events": [103]}}]
        sc = {"error": None, "source": "streamline", "shaders": [
            {"mode": "colour", "events": [100, 101], "pipes": {"A": 4e6, "T": 1e6}},
            {"mode": "draw", "events": [100, 101], "pipes": {"A": 6e6}},
            {"mode": "draw", "events": [103], "pipes": {"LS": 2e6}},
            {"mode": "colour", "events": [999], "pipes": {"A": 9e6}, "error": "x"}]}
        self.assertTrue(frame_cost.measured(c, sc, events, G78))
        got = {r["index"]: r["cost"][G78] for r in c["events"]}
        self.assertEqual(sorted(got), [0, 1, 3])
        self.assertAlmostEqual(got[0]["fragment"], 4.5e6)  # more pixels than vertices: the rest is raster work
        self.assertAlmostEqual(got[0]["px_price"], 1.5e4)
        self.assertAlmostEqual(got[0]["vertex"], 0.0)
        self.assertEqual(got[0]["px_bound"], ["arith"])
        self.assertAlmostEqual(got[3]["total"], 2e6)
        self.assertEqual(c["geometry"], [])
        self.assertEqual([x["name"] for x in c["cores"]], [G78])
        self.assertAlmostEqual(c["totals"][G78]["total"], 8e6)

    def test_nothing_measured(self):
        self.assertFalse(frame_cost.measured({}, {"error": "hung", "shaders": []}, [], G78))
        self.assertFalse(frame_cost.measured({}, None, [], G78))


if __name__ == "__main__":
    unittest.main()
