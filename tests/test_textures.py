"""core/textures.py: the textures of a draw and their flags."""
import unittest

from paretogpu.core import textures


def tex(id_, name, fmt, w, h, mips=1, native=False, target=False):
    return {"id": id_, "name": name, "format": fmt, "width": w, "height": h, "mips": mips, "cube": False,
            "native": native, "color_target": target, "depth_target": False}


TEXTURES = [tex(1, "albedo", "R8G8B8A8_SRGB", 512, 512, mips=10), tex(2, "lut", "R8G8B8A8_UNORM", 2048, 64),
            tex(3, "_CameraColor", "R11G11B10_FLOAT", 1080, 2400, target=True), tex(4, "data", "R16G16B16A16_FLOAT", 512, 512),
            tex(5, "white", "R8G8B8A8_UNORM", 4, 4)]


def capture(pixels, textures_=TEXTURES):
    return {"textures": textures_,
            "actions": [{"event": 10, "ps_invocations": pixels, "textures": [
                {"image": 1, "stage": "frag", "filter": "aniso 8x"}, {"image": 2, "stage": "frag", "filter": "bilinear"},
                {"image": 3, "stage": "frag", "filter": "point"}, {"image": 5, "stage": "frag", "filter": "bilinear"}]},
                        {"event": 11, "ps_invocations": pixels, "textures": [
                {"image": 4, "stage": "vert", "filter": None}, {"image": 1, "stage": "frag", "filter": None}]}]}


def run(formats, pixels=25000, cap=None):
    rows = [{"index": 0, "kind": "draw"}]
    textures.attach(rows, [{"index": 0, "rd": {"events": [10, 11]}}], cap or capture(pixels), formats)
    return {t["name"]: t for t in rows[0]["textures"]}


class Textures(unittest.TestCase):
    def test_pc_formats_from_the_importer(self):
        t = run({"albedo": [{"path": "a.png", "format": "ASTC_6x6"}], "lut": [{"format": "RGBA32"}]})
        self.assertEqual((t["albedo"]["format"], t["albedo"]["source"], t["albedo"]["flags"]),
                         ("ASTC_6x6", "android", []))
        self.assertEqual(t["albedo"]["filter"], "aniso 8x")  # the call with a sampler wins over the fetch
        self.assertEqual(t["lut"]["flags"], ["uncompressed", "no_mips"])  # 131k texels > 25k pixels of its call
        self.assertEqual((t["_CameraColor"]["target"], t["_CameraColor"]["flags"]), (True, []))
        self.assertEqual(t["data"]["flags"], [])  # read by the vertex stage
        self.assertEqual(t["white"]["flags"], [])  # 16 texels: magnified, stays in the cache

    def test_magnified_is_not_flagged(self):
        self.assertEqual(run({}, pixels=400000)["lut"]["flags"], [])

    def test_a_call_without_pixels_does_not_flag(self):
        cap = capture(25000)
        cap["actions"].append({"event": 12, "ps_invocations": 0, "textures": [{"image": 5, "stage": "frag",
                                                                              "filter": "bilinear"}]})
        rows = [{"index": 0, "kind": "draw"}]
        textures.attach(rows, [{"index": 0, "rd": {"events": [10, 11, 12]}}], cap, {})
        self.assertEqual(next(t for t in rows[0]["textures"] if t["name"] == "white")["flags"], [])

    def test_without_the_editor(self):
        t = run(None)
        self.assertEqual((t["albedo"]["format"], t["albedo"]["source"]), ("R8G8B8A8_SRGB", "editor"))
        self.assertEqual(t["albedo"]["flags"], ["uncompressed"])  # 262k texels: the editor's RGBA8 looks uncompressed

    def test_device_formats_as_captured(self):
        t = run({}, cap=capture(25000, [{**x, "native": True} for x in TEXTURES]))
        self.assertEqual({k: v["source"] for k, v in t.items()}, dict.fromkeys(t, "capture"))
        self.assertEqual(t["albedo"]["format"], "R8G8B8A8_SRGB")

    def test_asset_names_leave_out_targets(self):
        self.assertEqual(textures.asset_names(capture(1)), ["albedo", "data", "lut", "white"])


if __name__ == "__main__":
    unittest.main()
