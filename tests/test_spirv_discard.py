"""adapters/renderdoc/rd_spirv.py: removing fragment shader discards in SPIR-V."""
import glob
import os
import subprocess
import tempfile
import unittest

from paretogpu.adapters.renderdoc import rd_spirv

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "spirv")


def spirv_val():
    hits = glob.glob(r"C:\Program Files\Unity\Hub\Editor\*\Editor\Data\PlaybackEngines\AndroidPlayer\NDK"
                     r"\shader-tools\windows-x86_64\spirv-val.exe")
    return hits[0] if hits else None


def read(name):
    with open(os.path.join(DATA, name), "rb") as f:
        return f.read()


class DiscardTest(unittest.TestCase):
    def test_discards_turn_into_returns(self):
        raw = read("alpha_clip.frag.spv")
        self.assertTrue(rd_spirv.discards(raw))
        new = rd_spirv.without_discard(raw)
        self.assertEqual(len(new), len(raw))
        self.assertFalse(rd_spirv.discards(new))
        val = spirv_val()
        if val:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "x.spv")
                with open(path, "wb") as f:
                    f.write(new)
                r = subprocess.run([val, "--target-env", "vulkan1.1", path], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_nothing_to_remove(self):
        raw = read("loop_lights.frag.spv")
        self.assertFalse(rd_spirv.discards(raw))
        self.assertIsNone(rd_spirv.without_discard(raw))

    def test_stubs(self):
        z, off = rd_spirv.zero_outputs, rd_spirv.off_screen
        for name, make in (("alpha_clip.frag.spv", z), ("loop_lights.frag.spv", z), ("tex_x24.frag.spv", z),
                           ("wave_pos.vert.spv", z), ("wave_pos.vert.spv", off)):
            raw = read(name)
            new = make(raw)
            self.assertGreater(len(new), len(raw))
            self.assertEqual(rd_spirv.discards(new), rd_spirv.discards(raw))
            val = spirv_val()
            if val:
                with tempfile.TemporaryDirectory() as d:
                    path = os.path.join(d, "x.spv")
                    with open(path, "wb") as f:
                        f.write(new)
                    r = subprocess.run([val, "--target-env", "vulkan1.1", path], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, name + ": " + r.stdout + r.stderr)

    def test_not_spirv(self):
        with self.assertRaises(ValueError):
            rd_spirv.discards(b"not a shader")


if __name__ == "__main__":
    unittest.main()
