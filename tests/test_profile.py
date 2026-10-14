"""core/profile.py on synthetic data."""
import unittest

from paretogpu.core import profile


def meta(build_target="Android", screen=(2000, 1000)):
    return {"quality": "Q", "build_target": build_target, "screen": list(screen), "pipeline": {"name": "RP"}}


def row(index, path, stage="opaque", size=(1000, 500), pixels=100):
    return {"index": index, "path": path, "stage": stage, "rt_size": list(size), "pixels": pixels}


class Profile(unittest.TestCase):
    def test_describe(self):
        info = profile.describe(meta(), [1176, 588])
        self.assertEqual((info["pipeline"], info["frame_scale"]), ("RP", 0.588))
        self.assertEqual(profile.warnings(info), [])
        self.assertIn("0.588", profile.summary(info))

    def test_build_target(self):
        w = profile.warnings(profile.describe(meta(build_target="StandaloneWindows64"), [2000, 1000]))
        self.assertEqual([(x["code"], x["target"]) for x in w], [("build_target_not_mobile", "StandaloneWindows64")])

    def test_other_aspect_has_no_scale(self):
        self.assertIsNone(profile.describe(meta(), [1000, 1000])["frame_scale"])

    def test_frame_size(self):
        rows = [{"rt_size": [100, 100], "pixels": 10}, {"rt_size": [50, 50], "pixels": 500}, {"rt_size": [None, None]}]
        self.assertEqual(profile.frame_size(rows), [50, 50])
        self.assertIsNone(profile.frame_size([]))
        scaled = [{"rt_size": [1000, 500], "pixels": 400000, "stage": "opaque"},
                  {"rt_size": [2000, 1000], "pixels": 2000000, "stage": "post"}]
        self.assertEqual(profile.frame_size(scaled), [1000, 500])

    def test_render_and_differences(self):
        cam = "Cam/ExecuteRenderGraph"
        rows = [row(0, f"{cam}/(RP 0:0) Main Light Shadow/Shadows.Draw", "shadow", (1024, 1024)),
                row(1, f"{cam}/(RP 1:0) Draw Opaque/RenderLoop.Draw"),
                row(2, f"{cam}/(RP 2:0) SSAO", "post")]
        memory = {"passes": [{"targets": [{"width": 1000, "height": 500, "samples": 1, "format": "R11G11B10_FLOAT"}]}]}
        a = profile.render(rows, memory, [2000, 1000])
        self.assertEqual(a, {"size": [1000, 500], "scale": 0.5, "msaa": 1, "color": "R11G11B10_FLOAT",
                             "shadow_maps": [[1024, 1024]], "passes": ["Main Light Shadow", "Draw Opaque", "SSAO"]})
        b = profile.render(rows[:2], memory, [2000, 1000])
        self.assertEqual(profile.differences(a, b), [{"what": "passes", "a": ["SSAO"], "b": []}])


if __name__ == "__main__":
    unittest.main()
