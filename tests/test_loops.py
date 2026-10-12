"""core/loops.py: dynamic loops forced to n iterations, in GLSL text and binary SPIR-V (corpus/synthetic
loop_dynamic.frag and its glslang SPIR-V in testdata/spirv). The malioc and spirv-val checks are skipped when
the tools are not installed.

Run: python -m unittest discover tests   (from the repository root)
"""
import glob
import os
import struct
import subprocess
import tempfile
import unittest

from paretogpu.adapters import malioc as mali
from paretogpu.app import rules
from paretogpu.core import loops

HERE = os.path.dirname(os.path.abspath(__file__))
GLSL = os.path.join(HERE, "..", "paretogpu", "corpus", "synthetic", "loop_dynamic.frag")
SPV = os.path.join(HERE, "data", "spirv", "loop_dynamic.frag.spv")
STATIC_SPV = os.path.join(HERE, "data", "spirv", "tex_x24.frag.spv")


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
        word = loops.force_glsl(src, 1, nested=2)
        self.assertEqual((word.count("<1;"), word.count("<2;")), (1, 1))

    def test_nested_loop_is_out_of_the_slope(self):
        # terrain probe loop on G78: base 10, at 0 -> 4, at 2 -> 15, its nested scan twice -> 12
        p = {"base": {"ls": 10.0}, "loops": [{"zero": {"ls": 4.0}, "two": {"ls": 15.0}, "word": {"ls": 12.0}}]}
        self.assertEqual(loops.cycles_at(p, 3)["ls"], 10.0 + 2 * 3.0)
        self.assertEqual(loops.cycles_at(p, 0)["ls"], 4.0)
        p["loops"][0]["word"] = None  # no nested loop: the whole step is the slope
        self.assertEqual(loops.cycles_at(p, 3)["ls"], 10.0 + 2 * 5.0)

    def test_fractional_n_is_linear(self):
        p = {"base": {"ls": 10.0}, "loops": [{"zero": {"ls": 4.0}, "two": {"ls": 15.0}, "word": None}]}
        self.assertEqual(loops.cycles_at(p, 0.5)["ls"], 10.0 - 0.5 * 6.0)  # between 0 and 1 repeats
        self.assertEqual(loops.cycles_at(p, 1.5)["ls"], 10.0 + 0.5 * 5.0)

    def test_pixels_split_over_calls(self):
        from paretogpu.app.frame_loops import _split
        self.assertEqual(_split(16, [(1, 1225346), (2, 48044), (3, 0)]), [(1, 15), (2, 1)])
        many = _split(16, [(i, 100 + i) for i in range(60)])
        self.assertEqual(sum(n for _, n in many), 16)  # a batch of many calls: still 16 pixels
        self.assertEqual(_split(16, [(1, 0)]), [])

    def test_every_loop_has_its_n(self):
        p = {"base": {"ls": 10.0, "fma": 5.0},
             "loops": [{"zero": {"ls": 6.0, "fma": 4.0}, "two": {"ls": 13.0, "fma": 6.0}, "word": None},
                       {"zero": {"ls": 9.0, "fma": 3.0}, "two": {"ls": 11.0, "fma": 7.0}, "word": None}]}
        self.assertEqual(loops.cycles_at(p, [0, 3]), {"ls": 10.0 - 4.0 + 2 * 1.0, "fma": 5.0 - 1.0 + 2 * 2.0})
        self.assertEqual(loops.cycles_at(p, 2), loops.cycles_at(p, [2, 2]))  # one n for all is the same formula
        self.assertEqual(loops.cycles_at(p, 1), p["base"])

    def test_plan_and_forcing_per_loop(self):
        src = "void main(){ while(true){ if(a) break; while(true){ if(b) break; } } for(int i=0;i<uN;i++){ x+=1.0; } }"
        self.assertEqual(loops.outer_loops(src), [{"nested": True}, {"nested": False}])
        self.assertEqual([(m, k) for m, k, _, _ in loops.plan(src)],
                         [("base", None), ("zero", 0), ("two", 0), ("word", 0), ("zero", 1), ("two", 1)])
        out = loops.force_glsl(src, [3, 0])
        self.assertEqual((out.count("<3;"), out.count("<1;"), out.count("<0;")), (1, 1, 1))  # nested: once
        word = loops.force_glsl(src, 1, [2, 1])
        self.assertEqual((word.count("<2;"), word.count("<1;")), (1, 2))
        p = {"base": {"x": 1.0}, "loops": [{"zero": {"x": 0.5}, "two": {"x": 2.0}, "word": {"x": 1.0}},
                                            {"zero": {"x": 0.8}, "two": {"x": 1.1}, "word": None}]}
        self.assertEqual(loops.profile(loops.plan(src), [{"x": v} for v in (1.0, 0.5, 2.0, 1.0, 0.8, 1.1)])["loops"],
                         p["loops"])

    def test_bounds_traced_to_uniforms(self):
        src = """uniform 	vec4 CR_Params0;
uniform 	uint _WaveCount;
UNITY_BINDING(0) uniform Block {
	vec4                _Arr[16];
	float                _Scale;
};
uvec4 u_xlatu1;
void main(){
    u_xlatu1.xw = uvec2(CR_Params0.zw);
    if(c){ u_xlatu1.y = 7u; }
    while(true){ if(a) break; while(true){ if(b) break; } }
    u_xlatu1.x = min(u_xlatu1.w, 128u);
    for(uint u_xlatu_loop_1 = 0u ; u_xlatu_loop_1<u_xlatu1.x ; u_xlatu_loop_1++){ x += 1.0; }
    for(uint i = 0u ; i<=_WaveCount ; i++){ x += 1.0; }
    for(uint j = 0u ; j<u_xlatu1.y ; j++){ x += 1.0; }
}"""
        b = loops.glsl_bounds(src)
        self.assertEqual([loops.bound_text(x) for x in b], [None, "min(CR_Params0.w, 128)", "_WaveCount + 1", None])
        params = {"CR_Params0": [1.0, 2.0, 0.0, 300.0], "_WaveCount": 4}
        self.assertEqual([loops.bound_value(x, params) for x in b], [None, 128.0, 5.0, None])
        self.assertIsNone(loops.bound_value(b[1], {}))  # the event has no such parameter: default n

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
        p = rules.parametric(loops.forced(src), "vulkan", "fragment", "Mali-G78")
        self.assertIsNotNone(p)
        self.assertEqual(len(p["loops"]), 1)
        lp = p["loops"][0]
        c0, c1, c2 = lp["zero"]["sfu"], p["base"]["sfu"], lp["two"]["sfu"]
        self.assertIsNone(lp["word"])  # no nested loops
        self.assertLess(c0, c1)
        self.assertAlmostEqual(c2 - c1, c1 - c0, delta=0.25 * (c1 - c0))
        self.assertAlmostEqual(loops.cycles_at(p, 4)["sfu"], c1 + 3 * (c2 - c1))
        self.assertEqual(loops.cycles_at(p, 0)["sfu"], c0)

if __name__ == "__main__":
    unittest.main()
