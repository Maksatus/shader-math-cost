"""The phone frame as a snapshot (core/device.py)."""
import os
import unittest

from paretogpu.core import device
from paretogpu.core import memory
from paretogpu.model.variant import CODE, VariantKey
from paretogpu.store import workspace

DRAW = 0x2


# rd_device.py answer: depth-only shadow pass, opaque pass with 2 subpasses, post pass; secondary cmd buffers inside
def act(event, name, path, kinds, **more):
    return {"event": event, "name": name, "path": path, "flags": DRAW if "draw" in kinds else 0, "kinds": kinds,
            "indices": more.pop("indices", 0), "instances": more.pop("instances", 0), **more}


CAM = "FrameTime.GPU/SlotsRenderPipeline.RenderSingleCamera: camera/ExecuteRenderGraph"
LIT = "Kefir/Lit (PBR)"
SECONDARY = CAM + "/vkCmdExecuteCommands(1)"
FRAME = {"device": "samsung SM-G996B", "serial": "X", "api": "Vulkan", "screen": [2400, 1080], "rotation": 90,
         "capture": "D:/snap/frame.rdc",
         "actions": [
             act(10, "vkCmdBeginRenderPass(D=Clear)", CAM, ["begin_pass"]),
             act(11, "=> vkCmdExecuteCommands()[0]: vkBeginCommandBuffer(ResourceId::1)", SECONDARY, ["begin_pass"]),
             act(12, "vkCmdDrawIndexed()", SECONDARY + "/Main Light Shadow/Shadows.Draw", ["draw"], indices=36,
                 instances=1),
             act(13, "vkCmdEndRenderPass(D=Store)", CAM, ["end_pass"]),
             act(20, "vkCmdBeginRenderPass(C=Clear, DS=Clear)", CAM, ["begin_pass"]),
             act(21, "vkCmdDrawIndexed()", SECONDARY + "/Draw Opaque/RenderLoop.Draw", ["draw"], indices=60,
                 instances=2),
             act(22, "vkCmdNextSubpass() => 1", CAM, ["begin_pass", "end_pass"]),
             act(23, "vkCmdDraw()", SECONDARY + "/Draw Skybox", ["draw"], indices=3, instances=1),
             act(24, "vkCmdEndRenderPass(C=Store, DS=Store)", CAM, ["end_pass"]),
             act(25, "vkCmdPipelineBarrier()", CAM, []),
             act(30, "vkCmdBeginRenderPass(Load)", CAM, ["begin_pass"]),
             act(31, "vkCmdDraw()", SECONDARY + "/Uber PP", ["draw"], indices=3, instances=1),
             act(32, "vkCmdEndRenderPass(Store)", CAM, ["end_pass"]),
             act(40, "vkCmdBeginRenderPass(Load)", CAM, ["begin_pass"]),
             act(41, "vkCmdDrawIndexed()", CAM + "/DrawScreenSpaceUI/GUI.Repaint/GUITexture.Draw", ["draw"],
                 indices=6, instances=1),
             act(42, "vkCmdEndRenderPass(Store)", CAM, ["end_pass"])],
         "draws": {
             "12": {"shaders": {"vert": {"hash": "a1", "file": "a1.vert.spv", "name": LIT + device.SHARED}},
                    "blocks": {}, "rt": {"id": 5, "name": "_Shadowmap", "width": 1024, "height": 1024, "samples": 1,
                                         "count": 0, "backbuffer": False},
                    "blend": "One Zero", "meshes": ["rock"], "unique_vertices": 24, "indexed": True,
                    "samples_passed": 5000},
             "21": {"shaders": {"vert": {"hash": "b1", "file": "b1.vert.spv", "name": LIT + device.SHARED},
                                "frag": {"hash": "b2", "file": "b2.frag.spv", "name": "Kefir/Lit (PBR)"}},
                    "blocks": {"@1.0": "AAAA"}, "rt": {"id": 6, "name": "CameraColor", "width": 1411, "height": 635,
                                                       "samples": 4, "count": 1, "backbuffer": False},
                    "blend": "One Zero", "meshes": ["house", "house"], "unique_vertices": 40, "indexed": True,
                    "samples_passed": 4000, "discards": True, "samples_shaded": 6000},
             "23": {"shaders": {"vert": {"hash": "c1", "file": "c1.vert.spv", "name": "Hidden/Sky"},
                                "frag": {"hash": "c2", "file": "c2.frag.spv", "name": "Hidden/Sky"}},
                    "rt": {"id": 6, "name": "CameraColor", "width": 1411, "height": 635, "samples": 1, "count": 1},
                    "blend": "One Zero", "meshes": [], "unique_vertices": None, "indexed": False,
                    "samples_passed": 300},
             "31": {"shaders": {"vert": {"hash": "d1", "file": "d1.vert.spv", "name": "Hidden/Kefir/UberPost"},
                                "frag": {"hash": "d2", "file": "d2.frag.spv", "name": "Hidden/Kefir/UberPost"}},
                    "rt": {"id": 7, "name": "Swapchain Image 1", "width": 2400, "height": 1080, "samples": 1,
                           "count": 1, "backbuffer": True},
                    "blend": "One Zero", "meshes": [], "unique_vertices": None, "indexed": False,
                    "samples_passed": 2592000},
             "41": {"shaders": {"vert": {"hash": "e1", "file": "e1.vert.spv", "name": "Hidden/Internal-GUITexture"},
                                "frag": {"hash": "e2", "file": "e2.frag.spv", "name": "Hidden/Internal-GUITexture"}},
                    "rt": {"id": 7, "name": "Swapchain Image 1", "width": 2400, "height": 1080, "samples": 1,
                           "count": 1, "backbuffer": True},
                    "blend": "One Zero", "meshes": [], "unique_vertices": 4, "indexed": True,
                    "samples_passed": 1955}},
         "discard_error": None,
         "textures": [{"id": 6, "name": "CameraColor", "native": True}],
         "usage": {"6": [[20, "Clear"], [21, "ColorTarget"]]}}


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.snap, self.actions, self.mem = device.snapshot(FRAME, {"project": "D:/game",
                                                                     "gpu": "Mali-G78", "seconds": 50})
        self.ev = self.snap["events"]

    def test_render_passes_by_name(self):
        # RenderDoc flags secondary cmd buffers and subpasses as passes too: names decide
        rp = device.render_passes(FRAME["actions"])
        self.assertEqual((rp[12], rp[21], rp[23], rp[31]), ([0, 0], [1, 0], [1, 1], [2, 0]))
        self.assertIsNone(rp[25])  # between the passes

    def test_paths_like_the_editor(self):
        self.assertEqual([e["path"].split("ExecuteRenderGraph/")[1] for e in self.ev],
                         ["(RP 0:0) Main Light Shadow/Shadows.Draw", "(RP 1:0) Draw Opaque/RenderLoop.Draw",
                          "(RP 1:1) Draw Skybox", "(RP 2:0) Uber PP"])
        self.assertEqual([e["stage"] for e in self.ev], ["shadow", "opaque", "opaque", "post"])

    def test_events(self):
        shadow, lit, sky, uber = self.ev
        self.assertEqual(lit["shader"], "Kefir/Lit (PBR)")  # the fragment module, not the shared vertex one
        self.assertEqual(shadow["shader"], "Kefir/Lit (PBR) (shared shader)")  # depth only: whose it is, identify()
        self.assertEqual(shadow["code"], {"vert": "device/shaders/a1.vert.spv", "hash": shadow["code"]["hash"]})
        self.assertEqual(lit["rd"]["ps_invocations"], 1500)  # 6000 samples of a 4x MSAA target
        self.assertEqual((shadow["rd"]["ps_invocations"], shadow["pixel_count"], shadow["rd"]["samples"]), (0, 0, 5000))
        self.assertIsNone(lit["global_keywords"])  # not in the capture
        self.assertEqual(lit["rd"]["samples"], 4000)
        self.assertEqual((lit["vertices"], lit["indices"], lit["rd"]["vertex_method"]), (80, 120, "indices"))
        self.assertEqual((sky["vertices"], sky["rd"]["vertex_method"]), (3, "calls"))
        self.assertEqual(lit["params"], {"@1.0": "AAAA"})
        self.assertEqual(lit["object"], "house")
        self.assertTrue(uber["rt"]["backbuffer"])
        self.assertEqual(VariantKey.of(lit).keywords, (CODE + lit["code"]["hash"],))
        named = dict(lit, code={**lit["code"], "variant": ["S", 0, 2, "Forward", ["_A"]]})
        self.assertEqual(VariantKey.of(named), VariantKey("S", 0, 2, "Forward", ("_A",)))

    def test_meta(self):
        self.assertEqual(self.snap["renderdoc"]["capture"], "frame.rdc")
        self.assertEqual(self.snap["renderdoc"]["replay"], "X")
        self.assertEqual((self.snap["screen"], self.snap["rotation"]), ([2400, 1080], 90))
        self.assertIsNone(self.snap["quality"])
        self.assertEqual(self.snap["device"]["imgui_draws"], 1)
        self.assertEqual(self.snap["device"]["discard_unshaded"], 0)

    def test_hardware_without_counters(self):
        self.assertIsNone(device.hardware({}, FRAME["actions"], "Mali-G78"))
        crashed = device.hardware({"mali": {"names": [], "passes": {}, "error": "crashed"}}, FRAME["actions"],
                                  "Mali-G78")
        self.assertEqual(crashed, {"core": "Mali-G78", "error": "crashed", "shader_cost": None})

    def test_shader_cost_by_swap(self):
        names = [device.EXEC, "Arithmetic unit issue cycles", "Texture unit issue cycles"]
        lit = self.ev[1]["rd"]["events"][0]
        ko = {"names": names, "base": {"10": [400, 100, 0], "20": [900, 300, 80], "30": [50, 40, 5]},
              "spread": {"10": 8, "20": 20, "30": 1},
              "shaders": [{"file": "b2.frag.spv", "events": [lit],
                           "passes": {"10": [404, 100, 0], "20": [600, 120, 30], "30": [51, 40, 5]}},
                          {"file": "c3.frag.spv", "events": [], "error": "no_output"},
                          {"stage": "vert", "mode": "draw", "file": "d4.vert.spv", "events": [lit],
                           "passes": {"20": [700, 250, 70]}}]}
        dev = {"mali": {"names": names, "passes": {"10": [400, 100, 0], "20": [900, 300, 80], "30": [50, 40, 5]},
                        "error": None}, "knockout": ko}
        sc = device.hardware(dev, FRAME["actions"], "Mali-G78")["shader_cost"]
        top, broken, draw = sc["shaders"]
        self.assertEqual((top["mode"], top["file"], top["pipes"]), ("colour", "b2.frag.spv", {"A": 180, "T": 50}))
        self.assertEqual((draw["stage"], draw["mode"], draw["pipes"]), ("vert", "draw", {"A": 50, "T": 10}))
        self.assertEqual((broken["error"], broken["pipes"]), ("no_output", {"A": 0.0, "T": 0.0}))
        self.assertEqual(sc["source"], "renderdoc")
        self.assertEqual(device.hardware({**dev, "knockout": {"error": "crashed"}}, FRAME["actions"],
                                         "Mali-G78")["shader_cost"], {"error": "crashed", "shaders": []})

    def test_shader_cost_of_the_whole_frame(self):
        lit = self.ev[1]["rd"]["events"][0]
        names = [device.EXEC, "Arithmetic unit issue cycles", "Texture unit issue cycles"]
        ko = {"names": names, "base": {"frame": [1000, 300, 80]}, "spread": {"frame": 30}, "source": "streamline",
              "shaders": [{"stage": "frag", "mode": "colour", "file": "b2.frag.spv", "events": [lit],
                           "passes": {"frame": [995, 200, 80]}}]}
        dev = {"mali": {"names": [], "passes": {}, "error": "crashed"}, "knockout": ko}
        h = device.hardware(dev, FRAME["actions"], "Mali-G78")
        self.assertEqual((h["error"], h["shader_cost"]["source"]), ("crashed", "streamline"))
        self.assertEqual(h["shader_cost"]["shaders"][0]["pipes"], {"A": 100, "T": 0})

    def test_actions_and_memory(self):
        by = {a["event"]: a for a in self.actions["actions"]}
        self.assertIn("clear", by[20]["kinds"])  # a render pass clearing on load clears its targets
        self.assertNotIn("begin_pass", by[20]["kinds"])
        self.assertEqual(by[21]["ps_invocations"], 1500)
        self.assertTrue(self.mem["textures"][0]["native"])

    def test_capture_and_where_it_replays(self):
        rdc = os.path.join("D:/snap", "frame.rdc")
        self.assertEqual(workspace.capture_of("D:/snap", self.snap), (rdc, "X"))
        self.assertEqual(workspace.capture_of("D:/snap", {"renderdoc": {"capture": "frame.rdc", "replay": None}}),
                         (rdc, None))
        self.assertEqual(workspace.capture_of("D:/snap", {}), (None, None))

    def test_native_textures_as_they_are(self):
        rgba = {"name": "albedo", "width": 4, "height": 4, "bytes": 64, "format": "R8G8B8A8_UNORM"}
        self.assertEqual(memory.device_size(rgba)[2], "astc")  # no Android formats: the editor's RGBA8 counts as ASTC
        self.assertEqual(memory.device_size(dict(rgba, native=True))[1:], (64, "capture"))

    def test_pc_textures_in_their_android_format(self):
        rgba = {"name": "albedo", "width": 4, "height": 4, "bytes": 64, "format": "R8G8B8A8_UNORM"}
        self.assertEqual(memory.device_size(rgba, {"albedo": [{"format": "ASTC_8x8"}]})[1:], (4.0, "android"))
        self.assertEqual(memory.device_size(rgba, {"albedo": [{"format": "RGBA32"}]})[1:], (64.0, "android"))
        self.assertEqual(memory.device_size(rgba, {"albedo": []})[1:], (64, "capture"))  # not an asset: as captured


if __name__ == "__main__":
    unittest.main()
