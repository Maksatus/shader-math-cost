"""Memory traffic of the passes (K2.3) on a synthetic capture: no project data.

Run: python -m unittest discover tests   (from the repository root)
"""
import unittest

from paretogpu.core import memory


def tex(tid, name, w, h, fmt, nbytes, mips=1, color=False, depth=False):
    return {"id": tid, "name": name, "width": w, "height": h, "depth": 1, "mips": mips, "arrays": 1, "samples": 1,
            "format": fmt, "bytes": nbytes, "cube": False, "color_target": color, "depth_target": depth,
            "swapchain": False}


def action(eid, kinds, path="", ps=0):
    return {"event": eid, "name": "x", "path": path, "kinds": kinds, "ps_invocations": ps, "vs_invocations": 0}


# C: scene colour, Z: its depth, P: the post target, A: an asset texture (64x64 with mips)
TEXTURES = [tex(1, "C", 100, 100, "R8G8B8A8_UNORM", 40000, color=True),
            tex(2, "Z", 100, 100, "D32S8_TYPELESS", 80000, depth=True),
            tex(3, "P", 100, 100, "R8G8B8A8_UNORM", 40000, color=True),
            tex(4, "A", 64, 64, "R8G8B8A8_SRGB", 21844, mips=7)]
USAGE = {"1": [[10, "Clear"], [11, "ColorTarget"], [12, "ColorTarget"], [20, "PS_Resource"], [21, "ColorTarget"]],
         "2": [[10, "Clear"], [11, "DepthStencilTarget"], [12, "DepthStencilTarget"], [21, "DepthStencilTarget"]],
         "3": [[20, "ColorTarget"], [30, "PS_Resource"]],
         "4": [[11, "PS_Resource"], [12, "PS_Resource"]]}
ACTIONS = [action(3, ["draw"], "Editor"), action(10, ["clear"]), action(11, ["draw"], "Cam/Opaque", 5000),
           action(12, ["draw"], "Cam/Opaque", 5000), action(20, ["draw"], "Cam/Post", 10000),
           action(21, ["draw"], "Cam/Late", 100), action(30, ["draw"], "Editor", 10000)]
EVENTS = [{"index": 0, "path": "Cam/(RP 0:0) Draw Opaque/Loop", "rd": {"events": [11, 12]}},
          {"index": 1, "path": "Cam/(RP 1:0) Post", "rd": {"events": [20]}},
          {"index": 2, "path": "Cam/(RP 2:0) Late", "rd": {"events": [21]}}]
ROWS = [{"index": 0, "cost": {"Mali-G78": {"total": 1e6}}}, {"index": 1, "cost": {"Mali-G78": {"total": 2e5}}},
        {"index": 2, "cost": {"Mali-G78": {"total": 1e3}}}]
ASTC = memory.ASSET_BITS / 8


class Passes(unittest.TestCase):
    def setUp(self):
        self.m = memory.passes(TEXTURES, USAGE, ACTIONS, EVENTS, ROWS, ["Mali-G78", "Mali-X"])
        self.p = self.m["passes"]

    def test_passes_by_targets(self):
        self.assertEqual([p["name"] for p in self.p], ["Draw Opaque", "Post", "Late"])
        self.assertEqual([p["calls"] for p in self.p], [2, 1, 1])
        self.assertEqual([p["events"] for p in self.p], [[0], [1], [2]])

    def test_load_store(self):
        opaque, post, late = self.p
        t = {x["name"]: x for x in opaque["targets"]}
        self.assertTrue(t["C"]["cleared"] and t["Z"]["cleared"])
        self.assertFalse(t["C"]["load"] or t["Z"]["load"])
        self.assertTrue(t["C"]["store"] and t["Z"]["store"])  # sampled by Post, loaded by Late
        # colour 100x100x4 and depth 100x100x5 (not D3D11's 8), both AFBC x 0.5
        self.assertAlmostEqual(opaque["bytes"]["store"], 20000 + 25000)
        self.assertEqual(post["bytes"]["load"], 0)  # P holds nothing before: not loaded
        self.assertAlmostEqual(post["bytes"]["store"], 20000)  # read after the frame (the editor)
        self.assertAlmostEqual(late["bytes"]["load"], 20000 + 25000)
        self.assertEqual(late["bytes"]["store"], 0)  # nobody reads C or Z again: memoryless

    def test_textures(self):
        opaque, post, _ = self.p
        # an asset counts as ASTC: a texel per fragment, at most the texture (5461 texels of the mip chain) per call
        self.assertAlmostEqual(opaque["bytes"]["textures"], 2 * min(5000 * ASTC, 5461 * ASTC))
        self.assertAlmostEqual(post["bytes"]["textures"], 20000)  # C: 10000 fragments x 2 bytes, AFBC, capped at C

    def test_roofline(self):
        opaque = self.p[0]
        self.assertEqual(opaque["cycles"]["Mali-G78"], 1e6)
        r = opaque["roofline"]["Mali-G78"]
        self.assertAlmostEqual(r["alu_ms"], 1e6 / (14 * 854e6) * 1e3)
        self.assertAlmostEqual(r["mem_ms"], opaque["bytes"]["total"] / 51.2e9 * 1e3)
        self.assertEqual(r["bound"], "alu")
        self.assertIsNone(opaque["roofline"]["Mali-X"])  # no device known
        self.assertNotIn("Mali-X", self.m["totals"]["roofline"])
        self.assertAlmostEqual(sum(p["share"] for p in self.p), 1.0)
        self.assertEqual(self.m["totals"]["roofline"]["Mali-G78"]["memory_passes"],
                         sum(p["roofline"]["Mali-G78"]["bound"] == "memory" for p in self.p))

    def test_split_event_cycles(self):
        # an event whose calls fall into two passes gives each its share of the calls
        usage = {**USAGE, "3": [[12, "ColorTarget"], [20, "ColorTarget"], [30, "PS_Resource"]],
                 "1": [[10, "Clear"], [11, "ColorTarget"], [20, "PS_Resource"], [21, "ColorTarget"]]}
        m = memory.passes(TEXTURES, usage, ACTIONS, EVENTS, ROWS, ["Mali-G78"])
        self.assertEqual(len(m["passes"]), 4)
        self.assertAlmostEqual(m["passes"][0]["cycles"]["Mali-G78"], 5e5)

    def test_no_calls(self):
        self.assertIsNone(memory.passes(TEXTURES, USAGE, ACTIONS, [{"index": 0, "path": "", "rd": None}], [], []))


if __name__ == "__main__":
    unittest.main()
