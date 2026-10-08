"""frame/loops.py: dynamic loops forced to n iterations, in GLSL text and binary SPIR-V (corpus/synthetic
loop_dynamic.frag and its glslang SPIR-V in testdata/spirv). The malioc and spirv-val checks are skipped when
the tools are not installed.

Run: python -m unittest paretogpu.frame.test_loops   (from the repository root)
"""
import glob
import os
import struct
import subprocess
import tempfile
import unittest

from paretogpu import mali
from paretogpu.frame import loops

HERE = os.path.dirname(os.path.abspath(__file__))
GLSL = os.path.join(HERE, "..", "corpus", "synthetic", "loop_dynamic.frag")
SPV = os.path.join(HERE, "..", "testdata", "spirv", "loop_dynamic.frag.spv")
STATIC_SPV = os.path.join(HERE, "..", "testdata", "spirv", "tex_x24.frag.spv")


def spirv_val():
    hits = glob.glob(r"C:\Program Files\Unity\Hub\Editor\*\Editor\Data\PlaybackEngines\AndroidPlayer\NDK"
                     r"\shader-tools\windows-x86_64\spirv-val.exe")
    return hits[0] if hits else None


def read(path, binary=False):
    with open(path, "rb" if binary else "r") as f:
        return f.read()


class GlslTest(unittest.TestCase):
    def test_dynamic_for_is_forced(self):
        src = read(GLSL)
        out = loops.force_glsl(src, 3)
        self.assertNotIn("i < uCount", out)
        self.assertRegex(out, r"for\(int i = 0;_so_loop\d+<3; i\+\+\)\{ _so_loop\d+\+\+;")

    def test_while_true_and_nesting(self):
        src = "void main(){ while(true){ if(a) break; while(true){ if(b) break; } } }"
        out = loops.force_glsl(src, 4)
        self.assertEqual(out.count("<4;"), 1)  # outer
        self.assertEqual(out.count("<1;"), 1)  # nested: once per outer iteration

    def test_static_loop_is_kept(self):
        self.assertIsNone(loops.force_glsl("void main(){ for(int i = 0 ; i<4 ; i++){ x += 1.0; } }", 2))


class SpirvTest(unittest.TestCase):
    def test_forced_module(self):
        src = read(SPV, True)
        for n in (0, 1, 2):
            out = loops.force_spirv(src, n)
            self.assertIsNotNone(out)
            self.assertGreater(struct.unpack_from("<I", out, 12)[0], struct.unpack_from("<I", src, 12)[0])
            val = spirv_val()
            if val:
                fd, path = tempfile.mkstemp(suffix=".spv")
                os.write(fd, out)
                os.close(fd)
                try:
                    r = subprocess.run([val, path], capture_output=True, text=True)
                finally:
                    os.remove(path)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_dynamic_loop(self):
        self.assertIsNone(loops.force_spirv(read(STATIC_SPV, True), 2))

    @unittest.skipUnless(os.path.exists(mali.MALIOC), "malioc not installed")
    def test_price_is_linear_in_n(self):
        src = read(SPV, True)
        p = loops.parametric([loops.force(src, n) for n in loops.NS], "vulkan", "fragment", "Mali-G78")
        self.assertIsNotNone(p)
        c0, c1, c2 = (c["sfu"] for c in p["c"])
        self.assertLess(c0, c1)
        self.assertAlmostEqual(c2 - c1, c1 - c0, delta=0.25 * (c1 - c0))
        self.assertAlmostEqual(loops.cycles_at(p, 4)["sfu"], c1 + 3 * (c2 - c1))
        self.assertEqual(loops.cycles_at(p, 0)["sfu"], c0)


if __name__ == "__main__":
    unittest.main()
