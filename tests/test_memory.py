"""Memory traffic of the passes (core/memory.py) on a synthetic capture."""
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
def row(index, fragment, vertex):
    return {"index": index, "cost": {"Mali-G78": {"total": fragment + vertex, "fragment": fragment, "vertex": vertex,
                                                  "compute": 0.0}}}


ROWS = [row(0, 8e5, 2e5), row(1, 2e5, 0.0), row(2, 1e3, 0.0)]
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

    def test_cycles_are_the_events(self):
        # Late loads C and Z: load bytes, cycles only from its own event
        late = self.p[2]
        self.assertAlmostEqual(late["cycles"]["Mali-G78"], 1e3)
        self.assertGreater(late["bytes"]["load"], 0)
        self.assertEqual(self.p[0]["parts"]["Mali-G78"], {"fragment": 8e5, "vertex": 2e5, "compute": 0.0})

    def test_split_event_cycles(self):
        # event without an (RP x:y) marker, calls in two passes: each pass gets its share of the calls
        usage = {**USAGE, "3": [[12, "ColorTarget"], [20, "ColorTarget"], [30, "PS_Resource"]],
                 "1": [[10, "Clear"], [11, "ColorTarget"], [20, "PS_Resource"], [21, "ColorTarget"]]}
        events = [{**EVENTS[0], "path": "Cam/Draw Opaque/Loop"}] + EVENTS[1:]
        m = memory.passes(TEXTURES, usage, ACTIONS, events, ROWS, ["Mali-G78"])
        self.assertEqual(len(m["passes"]), 4)
        self.assertAlmostEqual(m["passes"][0]["cycles"]["Mali-G78"], 5e5)

    def test_native_pass_subpasses(self):
        usage = {"1": [[10, "Clear"], [11, "ColorTarget"], [13, "ColorTarget"], [14, "ColorTarget"], [20, "PS_Resource"]],
                 "2": [[10, "Clear"], [11, "DepthStencilTarget"], [13, "DepthStencilTarget"]],
                 "3": [[12, "Clear"], [13, "ColorTarget"], [14, "PS_Resource"], [20, "PS_Resource"]]}
        actions = [action(10, ["clear"]), action(11, ["draw"], "Cam/MRT", 100), action(12, ["clear"]),
                   action(13, ["draw"], "Cam/Opaque", 5000), action(14, ["draw"], "Cam/Sky", 5000),
                   action(20, ["draw"], "Cam/Post", 10000)]
        events = [{"index": 0, "path": "Cam/(RP 0:0) MRT Init", "rd": {"events": [11]}},
                  {"index": 1, "path": "Cam/(RP 0:1) Draw Opaque", "rd": {"events": [13]}},
                  {"index": 2, "path": "Cam/(RP 0:2) Draw Skybox", "rd": {"events": [14]}},
                  {"index": 3, "path": "Cam/(RP 1:0) Post", "rd": {"events": [20]}}]
        m = memory.passes(TEXTURES, usage, actions, events, [row(i, 1e3, 0.0) for i in range(4)], ["Mali-G78"])
        main = m["passes"][0]
        self.assertEqual(main["name"], "MRT Init + Draw Opaque + Draw Skybox")
        self.assertEqual(len(m["passes"]), 2)
        t = {x["name"]: x for x in main["targets"]}
        self.assertEqual(set(t), {"C", "Z", "P"})
        self.assertTrue(t["P"]["cleared"] and not t["P"]["load"])
        self.assertEqual(main["bytes"]["load"] + main["bytes"]["textures"], 0)
        self.assertTrue(t["C"]["store"] and t["P"]["store"] and not t["Z"]["store"])

    def test_resolve_on_tile(self):
        textures = [{**tex(5, "M", 100, 100, "R8G8B8A8_UNORM", 160000, color=True), "samples": 4},
                    tex(6, "R", 100, 100, "R8G8B8A8_UNORM", 40000, color=True), TEXTURES[2]]
        usage = {"5": [[10, "Clear"], [11, "ColorTarget"], [12, "ResolveSrc"]],
                 "6": [[12, "ResolveDst"], [20, "PS_Resource"]], "3": [[20, "ColorTarget"]]}
        actions = [action(10, ["clear"]), action(11, ["draw"], "Cam/Opaque", 5000), action(12, ["resolve"]),
                   action(20, ["draw"], "Cam/Post", 10000)]
        events = [{"index": 0, "path": "Cam/(RP 0:0) Draw Opaque", "rd": {"events": [11]}},
                  {"index": 1, "path": "Cam/(RP 1:0) Post", "rd": {"events": [20]}}]
        rows = [row(0, 1e3, 0.0), row(1, 1e3, 0.0)]
        m = memory.passes(textures, usage, actions, events, rows, ["Mali-G78"])
        opaque = m["passes"][0]
        self.assertEqual(opaque["resolves"], ["R"])
        self.assertFalse(opaque["targets"][0]["store"])
        self.assertAlmostEqual(opaque["bytes"]["store"], 20000)
        self.assertEqual((m["totals"]["copies"], m["totals"]["copy_calls"]), (0.0, 0))
        usage = {"5": [[10, "Clear"], [11, "ColorTarget"], [21, "ResolveSrc"]], "6": [[21, "ResolveDst"]],
                 "3": [[20, "ColorTarget"]]}
        actions = actions[:2] + [action(20, ["draw"], "Cam/Post", 10000), action(21, ["resolve"]),
                                 action(22, ["draw"], "Cam/Late", 100)]
        events = events + [{"index": 2, "path": "Cam/(RP 2:0) Late", "rd": {"events": [22]}}]
        m = memory.passes(textures, usage, actions, events, rows + [row(2, 1e3, 0.0)], ["Mali-G78"])
        self.assertTrue(m["passes"][0]["targets"][0]["store"])
        self.assertEqual(m["passes"][0]["resolves"], [])
        self.assertAlmostEqual(m["totals"]["copies"], 80000 + 20000)

    def run_frame(self, usage, actions, events, textures=TEXTURES):
        return memory.passes(textures, usage, actions, events, [row(e["index"], 1e3, 0.0) for e in events],
                             ["Mali-G78"])

    def test_clear_inside_a_pass_and_copy_of_its_target(self):
        usage = {"1": [[11, "ColorTarget"], [12, "ColorTarget"], [13, "ColorTarget"], [14, "CopySrc"],
                       [15, "ColorTarget"], [30, "PS_Resource"]],
                 "2": [[10, "Clear"], [11, "DepthStencilTarget"], [12, "Clear"], [13, "DepthStencilTarget"],
                       [20, "DepthStencilTarget"]],
                 "3": [[14, "CopyDst"], [20, "ColorTarget"], [30, "PS_Resource"]]}
        actions = [action(10, ["clear"], "Cam/Clear"), action(11, ["draw"], "Cam/A", 100),
                   action(12, ["clear"], "Cam/Clear"), action(13, ["draw"], "Cam/A", 100),
                   action(14, ["copy"], "Cam/Copy"), action(15, ["draw"], "Cam/A", 100),
                   action(20, ["draw"], "Cam/B", 100)]
        events = [{"index": 0, "path": "Cam/A", "rd": {"events": [11, 13, 15]}},
                  {"index": 1, "path": "Cam/B", "rd": {"events": [20]}}]
        m = self.run_frame(usage, actions, events)
        self.assertEqual([p["calls"] for p in m["passes"]], [2, 1, 1])
        first, after_copy, b = m["passes"]
        self.assertTrue({t["name"]: t for t in first["targets"]}["C"]["store"])
        self.assertTrue({t["name"]: t for t in after_copy["targets"]}["C"]["load"])
        self.assertTrue({t["name"]: t for t in b["targets"]}["Z"]["load"])

    def test_resolve_of_a_cleared_target_and_editor_work_before_the_frame(self):
        textures = [{**tex(5, "M", 100, 100, "R8G8B8A8_UNORM", 160000, color=True), "samples": 4},
                    tex(6, "R", 100, 100, "R8G8B8A8_UNORM", 40000, color=True), TEXTURES[2]]
        usage = {"5": [[8, "ResolveSrc"], [9, "Clear"], [10, "ResolveSrc"]], "6": [[8, "ResolveDst"], [10, "ResolveDst"]],
                 "3": [[20, "ColorTarget"]]}
        actions = [action(7, ["draw"], "UIR.DrawChain"), action(8, ["resolve"], "UIR.DrawChain"),
                   action(9, ["clear"], "Cam/Clear"), action(10, ["resolve"], "Cam/Resolve"),
                   action(20, ["draw"], "Cam/Post", 100)]
        m = self.run_frame(usage, actions, [{"index": 0, "path": "Cam/(RP 0:0) Post", "rd": {"events": [20]}}],
                           textures)
        self.assertEqual((m["totals"]["copies"], m["totals"]["copy_calls"]), (0.0, 0))

    def test_target_layer_and_texture_without_a_counter(self):
        array = {**tex(7, "Probes", 64, 64, "R8G8B8A8_UNORM", 64 * 64 * 4 * 16, color=True), "arrays": 16}
        usage = {"7": [[11, "ColorTarget"], [30, "PS_Resource"]], "4": [[11, "PS_Resource"]]}
        actions = [action(11, ["draw"], "Cam/Probe"), action(30, ["draw"], "Editor", 100)]
        actions[0]["ps_invocations"] = None
        m = self.run_frame(usage, actions, [{"index": 0, "path": "Cam/Probe", "rd": {"events": [11]}}],
                           [array, TEXTURES[3]])
        p = m["passes"][0]
        self.assertEqual(p["targets"][0]["bytes"], 64 * 64 * 4 * memory.AFBC)
        self.assertAlmostEqual(p["bytes"]["textures"], 5461 * ASTC)

    def test_event_split_by_the_pixels_of_its_calls(self):
        usage = {**USAGE, "3": [[12, "ColorTarget"], [20, "ColorTarget"], [30, "PS_Resource"]],
                 "1": [[10, "Clear"], [11, "ColorTarget"], [20, "PS_Resource"], [21, "ColorTarget"]]}
        actions = [dict(a) for a in ACTIONS]
        actions[2]["ps_invocations"], actions[3]["ps_invocations"] = 1000, 3000
        events = [{**EVENTS[0], "path": "Cam/Draw Opaque/Loop"}] + EVENTS[1:]
        m = memory.passes(TEXTURES, usage, actions, events, ROWS, ["Mali-G78"])
        self.assertAlmostEqual(m["passes"][0]["parts"]["Mali-G78"]["fragment"], 8e5 * 0.25)
        self.assertAlmostEqual(m["passes"][0]["parts"]["Mali-G78"]["vertex"], 2e5 * 0.5)

    def test_no_calls(self):
        self.assertIsNone(memory.passes(TEXTURES, USAGE, ACTIONS, [{"index": 0, "path": "", "rd": None}], [], []))


class DeviceFrame(unittest.TestCase):
    def test_swapchain_is_stored(self):
        # device frame: the swapchain image is presented, so stored
        sw = dict(tex(5, "Swapchain Image 1", 100, 100, "R8G8B8A8_UNORM", 40000, color=True), swapchain=True,
                  native=True)
        usage = {"5": [[11, "ColorTarget"]]}
        events = [{"index": 0, "path": "Cam/(RP 0:0) UI", "rd": {"events": [11]}}]
        m = memory.passes([sw], usage, [action(11, ["draw"], "Cam/UI", 100)], events, [row(0, 1e3, 0.0)], ["Mali-G78"])
        self.assertAlmostEqual(m["passes"][0]["bytes"]["store"], 40000 * memory.AFBC)
        self.assertEqual(m["assumptions"]["textures"], "native")
        # editor backbuffer: not stored
        m = memory.passes([dict(sw, native=False)], usage, [action(11, ["draw"], "Cam/UI", 100)], events,
                          [row(0, 1e3, 0.0)], ["Mali-G78"])
        self.assertEqual(m["passes"][0]["bytes"]["store"], 0)


class TileThreads(unittest.TestCase):
    """The VATyakov K31_Heavy passes whose Mali fragment threads the G78 counters show (K3.1)."""

    def test_msaa_resolve_and_depth_loads(self):
        def named(eid, name):
            return {"event": eid, "name": name, "path": "Cam", "kinds": []}
        texs = [dict(tex(1, "MS", 10, 10, "R11G11B10_FLOAT", 1600, color=True), samples=4),
                tex(2, "Resolved", 10, 10, "R11G11B10_FLOAT", 400, color=True),
                dict(tex(3, "MSZ", 10, 10, "D24S8", 1600, depth=True), samples=4),
                tex(4, "Z", 10, 10, "D32_SFLOAT", 400, depth=True), tex(5, "Post", 10, 10, "R8G8B8A8_UNORM", 400)]
        usage = {"1": [[10, "Clear"], [11, "ResolveSrc"], [21, "ColorTarget"], [22, "ResolveSrc"],
                       [31, "ColorTarget"], [32, "ResolveSrc"]],
                 "2": [[10, "Discard"], [11, "ResolveDst"], [22, "ResolveDst"], [32, "ResolveDst"]],
                 "3": [[20, "Clear"], [21, "DepthStencilTarget"], [31, "DepthStencilTarget"]],
                 "4": [[41, "DepthStencilTarget"]], "5": [[41, "ColorTarget"], [51, "ColorTarget"]]}
        acts = [named(10, "vkCmdBeginRenderPass(Clear)"), named(11, "vkCmdEndRenderPass(Store)"),
                named(20, "vkCmdBeginRenderPass(C=Load, DS=Clear)"), named(21, "vkCmdDrawIndexed()"),
                named(22, "vkCmdEndRenderPass(C=Store, DS=Store)"),
                named(30, "vkCmdBeginRenderPass(C=Load, DS=Load)"), named(31, "vkCmdDrawIndexed()"),
                named(32, "vkCmdEndRenderPass(C=Store, DS=Don't Care)"),
                named(40, "vkCmdBeginRenderPass(C=Load, D=Load)"), named(41, "vkCmdDraw()"),
                named(42, "vkCmdEndRenderPass(C=Store, D=Store)"),
                named(50, "vkCmdBeginRenderPass(Load)"), named(51, "vkCmdDraw()"), named(52, "vkCmdEndRenderPass(Store)")]
        got = {t["event"]: t["threads"] for t in memory.tile_threads(texs, usage, acts)}
        # clear + resolve 4 + 1, load + resolve 4 + 1, + MSAA depth load 4, depth-only load 1, colour load 0
        self.assertEqual(got, {10: 500, 20: 500, 30: 900, 40: 100})

    def test_editor_driver_padding(self):
        ms = dict(tex(1, "MS", 10, 10, "R11G11B10_FLOAT", 6400, color=True), samples=4)
        self.assertEqual(memory.target_size(ms), 400 * 4 * memory.AFBC)
        self.assertEqual(memory.target_size({**ms, "native": True}), 400 * 16 * memory.AFBC)
        self.assertEqual(memory.format_bytes("D32S8_TYPELESS"), 5)
        self.assertIsNone(memory.format_bytes("BC7_SRGB"))


if __name__ == "__main__":
    unittest.main()
