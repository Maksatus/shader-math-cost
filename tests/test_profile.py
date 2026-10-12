"""The settings of a frame snapshot and their warnings (core/profile.py) on synthetic metadata: no project data.

Run: python -m unittest discover tests   (from the repository root)
"""
import unittest

from paretogpu.core import compare, profile


def pipeline(scale=1.0, features=()):
    feats = [{"name": n, "class": "Feature", "fields": {"m_Active": on}, "refs": []} for n, on in features]
    return {"name": "RP", "class": "UniversalRenderPipelineAsset",
            "fields": {"m_RenderScale": scale, "m_SupportsHDR": True},
            "refs": [{"name": "Renderer", "class": "UniversalRendererData", "fields": {}, "refs": feats}]}


def meta(build_target="Android", screen=(2000, 1000), **kw):
    return {"quality": "Q", "quality_level": 1, "build_target": build_target, "screen": list(screen),
            "pipeline": pipeline(**kw)}


class Profile(unittest.TestCase):
    def test_describe(self):
        info = profile.describe(meta(scale=0.5, features=[("SSAO", True), ("Decals", False)]), [1000, 500])
        self.assertEqual((info["pipeline"], info["asset_scale"], info["frame_scale"]), ("RP", 0.5, 0.5))
        self.assertEqual(info["renderers"], ["Renderer"])
        self.assertEqual([(x["name"], x["active"]) for x in info["features"]], [("SSAO", True), ("Decals", False)])
        self.assertEqual(profile.warnings(info), [])
        self.assertIn("Decals (off)", profile.summary(info))

    def test_scale_set_elsewhere(self):
        info = profile.describe(meta(scale=1.0), [1176, 588])
        self.assertEqual(info["frame_scale"], 0.588)
        w = profile.warnings(info)
        self.assertEqual(len(w), 1)
        self.assertIn("0.588", w[0])

    def test_build_target(self):
        w = profile.warnings(profile.describe(meta(build_target="StandaloneWindows64"), [2000, 1000]))
        self.assertEqual(len(w), 1)
        self.assertIn("StandaloneWindows64", w[0])

    def test_no_pipeline_asset(self):
        m = meta()
        m["pipeline"] = None
        info = profile.describe(m, [2000, 1000])
        self.assertIsNone(info["asset_scale"])
        self.assertEqual(profile.warnings(info), [])

    def test_other_aspect_has_no_scale(self):
        self.assertIsNone(profile.describe(meta(), [1000, 1000])["frame_scale"])

    def test_old_snapshot(self):
        info = profile.describe({"quality": "Q"})
        self.assertFalse(info["recorded"])
        self.assertEqual(len(profile.warnings(info)), 1)
        self.assertIn("not recorded", profile.summary(info))

    def test_frame_size(self):
        rows = [{"rt_size": [100, 100], "pixels": 10}, {"rt_size": [50, 50], "pixels": 500}, {"rt_size": [None, None]}]
        self.assertEqual(profile.frame_size(rows), [50, 50])
        self.assertEqual(compare.resolution({"events": rows}), [50, 50])
        self.assertIsNone(profile.frame_size([]))
        # a scene at 0.5 upscaled to a full-size screen with more pixels: the scene's size, not the screen's
        scaled = [{"rt_size": [1000, 500], "pixels": 400000, "stage": "opaque"},
                  {"rt_size": [2000, 1000], "pixels": 2000000, "stage": "post"}]
        self.assertEqual(profile.frame_size(scaled), [1000, 500])


if __name__ == "__main__":
    unittest.main()
