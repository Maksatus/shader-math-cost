"""RenderDoc counters and call matching, stages, malioc errors and non-ASCII sources."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from paretogpu.adapters import malioc as mali
from paretogpu.app.rules import MeasureRule
from paretogpu.core import events as renderdoc

SYNTH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "glsl")


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
        # one extra RenderDoc draw on the path shifts the order: index counts disagree
        evs = [draw(0, "Cam/Opaque", 6), draw(1, "Cam/Opaque", 36)]
        acts = [action(10, "Cam/Opaque", 3), action(11, "Cam/Opaque", 6), action(12, "Cam/Opaque", 36)]
        matched, missing, mismatched = renderdoc.match(evs, acts, ["ps_invocations"])
        self.assertEqual((matched, missing, mismatched), (2, 0, 2))
        self.assertEqual(evs[0]["rd"]["count_mismatch"], [6, 3])

    def test_instances_and_batches_add_up(self):
        evs = [draw(0, "Cam/Opaque", 30, calls=2)]
        acts = [action(10, "Cam/Opaque", 6, instances=3), action(11, "Cam/Opaque", 12, instances=0)]
        self.assertEqual(renderdoc.match(evs, acts, []), (1, 0, 0))

    def test_non_indexed_and_full_screen_are_checked(self):
        blit = {**draw(0, "Cam/Bloom", 0), "vertices": 3, "shader": "Hidden/Bloom", "rt": {"width": 100, "height": 50}}
        evs = [blit, {**blit, "index": 1}]
        acts = [action(10, "Cam/Bloom", 6, ps=5000), action(11, "Cam/Bloom", 3, ps=20000)]
        self.assertEqual(renderdoc.match(evs, acts, ["ps_invocations"]), (2, 0, 2))
        self.assertEqual((evs[0]["rd"]["mismatch"], evs[0]["rd"]["count_mismatch"]), ("vertices", [3, 6]))
        self.assertEqual((evs[1]["rd"]["mismatch"], evs[1]["rd"]["count_mismatch"]), ("pixels", [5000, 20000]))

    def test_an_event_without_calls_does_not_take_later_ones(self):
        evs = [draw(0, "Cam/Shadows", 6), draw(1, "Cam/Opaque", 36)]
        acts = [action(10, "Cam/Opaque", 36), action(20, "Cam/Shadows", 6)]
        self.assertEqual(renderdoc.match(evs, acts, []), (1, 1, 0))
        self.assertIsNone(evs[0]["rd"])
        self.assertEqual(evs[1]["rd"]["events"], [10])

    def test_draws_under_a_marker_closed_early_take_the_calls_between(self):
        evs = [draw(0, "Cam/Transparent", 6), draw(1, "Cam/Transparent", 12), draw(2, "Cam/Transparent", 9, 2),
               draw(3, "Cam/TAA", 3)]
        acts = [action(10, "Cam/Transparent", 6), action(11, "Cam/RenderLoop.Draw", 12),
                action(12, "Cam/RenderLoop.Draw", 3), action(13, "Cam/RenderLoop.Draw", 6), action(20, "Cam/TAA", 3)]
        self.assertEqual(renderdoc.match(evs, acts, []), (4, 0, 0))
        self.assertEqual([e["rd"]["events"] for e in evs], [[10], [11], [12, 13], [20]])

    def test_calls_between_that_do_not_fit_are_not_taken(self):
        evs = [draw(0, "Cam/Transparent", 6), draw(1, "Cam/Transparent", 12), draw(2, "Cam/TAA", 3)]
        acts = [action(10, "Cam/Transparent", 6), action(11, "Cam/RenderLoop.Draw", 15), action(20, "Cam/TAA", 3)]
        self.assertEqual(renderdoc.match(evs, acts, []), (2, 1, 0))
        self.assertIsNone(evs[1]["rd"])

    def test_paths_match_whole_segments(self):
        evs = [draw(0, "Blit", 3), draw(1, "", 3)]
        acts = [action(10, "Cam/FinalBlit", 3), action(11, "Cam/Blit", 3), action(12, "", 3)]
        renderdoc.match(evs, acts, [])
        self.assertEqual((evs[0]["rd"]["events"], evs[1]["rd"]["events"]), ([11], [12]))


class StageTest(unittest.TestCase):
    def ev(self, path, shader, vertices=3, light_mode=None, rt="CameraColor", blend=None):
        return {"kind": "draw", "path": "Cam/ExecuteRenderGraph/" + path, "shader": shader, "vertices": vertices,
                "light_mode": light_mode, "rt": {"name": rt}, "blend": blend}

    def test_stages(self):
        evs = [self.ev("(RP 1:0) MRT Init", "Hidden/MRT"),
               self.ev("(RP 1:1) Draw Opaque/RenderLoop.Draw", "Lit", 9000, "Forward Opaque"),
               self.ev("(RP 1:2) Draw Skybox", "Hidden/Sky"),
               self.ev("(RP 2:0) Copy Depth", "Hidden/CopyDepth", rt="_CameraDepthTexture"),
               self.ev("(RP 3:0) Draw Fog", "Hidden/FogOpaque"),
               self.ev("(RP 4:0) Bloom", "Hidden/Bloom"),
               self.ev("DrawScreenSpaceUI/GUI.Repaint", "Hidden/TextCore/Distance Field SSD", 148,
                       blend="SrcAlpha OneMinusSrcAlpha")]
        self.assertEqual([e["stage"] for e in renderdoc.assign_stages(evs)],
                         ["opaque", "opaque", "opaque", "prepass", "post", "post", "ui"])


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
            r = MeasureRule(SYNTH).build_one(("sin_x4.frag", "Mali-G78"))
        self.assertEqual(r, {"file": "sin_x4.frag", "ok": False, "core": "Mali-G78", "api": "vulkan", "error": "not found"})

    def test_empty_core_list(self):
        with self.assertRaises(ValueError):
            mali.parse_cores(" , ")


if __name__ == "__main__":
    unittest.main()
