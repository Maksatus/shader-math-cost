"""core/loops.py: dynamic loops of SPIR-V forced to n iterations, loop bounds, loop pricing."""
import base64
import glob
import os
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from paretogpu.adapters import malioc as mali
from paretogpu.app import rules
from paretogpu.core import loops
from paretogpu.core import pricing

HERE = os.path.dirname(os.path.abspath(__file__))
SPV = os.path.join(HERE, "data", "spirv", "loop_dynamic.frag.spv")
STATIC_SPV = os.path.join(HERE, "data", "spirv", "tex_x24.frag.spv")
LIGHTS_SPV = os.path.join(HERE, "data", "spirv", "loop_lights.frag.spv")  # its source: loop_lights.frag
CLUSTER_SPV = os.path.join(HERE, "data", "spirv", "loop_cluster.frag.spv")  # its source: loop_cluster.frag
CAPPED_SPV = os.path.join(HERE, "data", "spirv", "loop_capped.frag.spv")  # its source: loop_capped.frag


def spirv_val():
    hits = glob.glob(r"C:\Program Files\Unity\Hub\Editor\*\Editor\Data\PlaybackEngines\AndroidPlayer\NDK"
                     r"\shader-tools\windows-x86_64\spirv-val.exe")
    return hits[0] if hits else None


def line(one, s, exit="header", zero=None, w=0.0):
    """Measurements of one loop whose real price is `one` once and `s` per more iteration (a number: "ls" only), its
    nested loop `w` per run; the others at 1."""
    m = loops.SLOPE_M
    one, s, w = ({"ls": v} if not isinstance(v, dict) else v for v in (one, s, w if w else {}))
    keys = set(one) | set(s)

    def at(n, nested=1):
        return {k: one.get(k, 0) + (n - 1) * (s.get(k, 0) + w.get(k, 0)) + n * (nested - 1) * w.get(k, 0)
                for k in keys}
    return {"exit": exit, "nested": bool(w), "at": m, "zero": {"ls": zero} if isinstance(zero, float) else zero,
            "m": at(m), "m2": at(2 * m), "m4": at(4 * m), "word": at(2 * m, 2) if w else None}


def prof(base, lps):
    return {"model": loops.MODEL, "base": base if isinstance(base, dict) else {"ls": base},
            "zero": None, "loops": lps}


def read(path, binary=False):
    with open(path, "rb" if binary else "r") as f:
        return f.read()


class ModelTest(unittest.TestCase):
    def test_nested_loop_is_out_of_the_slope(self):
        # Forward+ light loop: once 12, 3 per light, its nested word scan 2 per run (charged once)
        p = prof(10.0, [line(12.0, 3.0, "body", w=2.0)])
        self.assertAlmostEqual(loops.cycles_at(p, 3)["ls"], 12.0 + 2 * 3.0)
        p = prof(10.0, [line(12.0, 5.0, "body")])  # no nested loop: the whole step is the slope
        self.assertAlmostEqual(loops.cycles_at(p, 3)["ls"], 12.0 + 2 * 5.0)

    def test_slope_from_m_to_2m(self):
        # base 10: malioc unrolled the loop once and kept its uniform reads in registers; the real loop once is 14
        p = prof(10.0, [line(14.0, 3.0, zero=4.0)])
        self.assertAlmostEqual(loops.cycles_at(p, 1)["ls"], 14.0)
        self.assertAlmostEqual(loops.cycles_at(p, 3)["ls"], 20.0)
        self.assertAlmostEqual(loops.cycles_at(p, 0)["ls"], 4.0)
        self.assertAlmostEqual(loops.cycles_at(p, 0.5)["ls"], 9.0)  # between the measured zero and once
        self.assertAlmostEqual(loops.cycles_at(p, 1.5)["ls"], 15.5)

    def test_while_true_keeps_its_scan_below_one(self):
        # exit test in the body: at 0 lights the real shader still scans, the line goes on below 1
        p = prof(10.0, [line(12.0, 3.0, "body", w=2.0)])
        self.assertAlmostEqual(loops.cycles_at(p, 0)["ls"], 9.0)
        self.assertAlmostEqual(loops.cycles_at(p, 0.5)["ls"], 10.5)

    def test_ladder_steps(self):
        lp = line(3.0, 1.0)
        self.assertTrue(loops.agrees(loops.steps(lp)))
        lp["m4"] = dict(lp["m2"])  # stopped growing: a constant cap below 4M
        self.assertFalse(loops.agrees(loops.steps(lp)))
        lp = line(3.0, 1.0)
        lp["m2"] = None  # malioc N/A
        self.assertIsNone(loops.steps(lp))

    def test_negative_slope_is_zero(self):
        p = prof(10.0, [line(12.0, 0.0, zero=4.0)])
        lp = p["loops"][0]
        lp["m2"], lp["m4"] = {"ls": 7.0}, {"ls": 2.0}
        self.assertAlmostEqual(loops.cycles_at(p, 5)["ls"], loops.cycles_at(p, 1)["ls"])

    def test_reads_kept_in_registers_at_m(self):
        # one load per iteration, but at M iterations malioc still kept all of them in registers: the line is 2M..4M
        m = loops.SLOPE_M
        p = prof(2.0, [line(3.0, 1.0, zero=1.0)])
        p["loops"][0]["m"] = {"ls": 2.0}
        self.assertAlmostEqual(loops.cycles_at(p, 1)["ls"], 3.0)
        self.assertAlmostEqual(loops.cycles_at(p, m)["ls"], 2.0 + m)
        p = prof(2.0, [line(3.0, 1.0, zero=1.0)])
        p["loops"][0]["m4"]["ls"] -= 0.02 * 2 * m  # malioc a bit below the line at 4M: the line is M..2M
        self.assertAlmostEqual(loops.cycles_at(p, 1)["ls"], 3.0)

    def test_every_loop_has_its_n(self):
        p = prof({"ls": 10.0, "fma": 5.0}, [line({"ls": 10.0, "fma": 5.0}, {"ls": 3.0, "fma": 1.0},
                                                 zero={"ls": 6.0, "fma": 4.0}),
                                            line({"ls": 10.0, "fma": 5.0}, {"ls": 1.0, "fma": 2.0},
                                                 zero={"ls": 9.0, "fma": 3.0})])
        got = loops.cycles_at(p, [0, 3])
        self.assertAlmostEqual(got["ls"], 10.0 - 4.0 + 2 * 1.0)
        self.assertAlmostEqual(got["fma"], 5.0 - 1.0 + 2 * 2.0)
        self.assertEqual(loops.cycles_at(p, 2), loops.cycles_at(p, [2, 2]))  # one n for all is the same formula
        self.assertEqual(loops.cycles_at(p, 1), p["base"])

    def test_plan_and_forcing_per_loop(self):
        # while(true) with a nested loop, then a header-tested loop
        outer = [{"nested": True, "exit": "body"}, {"nested": False, "exit": "header"}]
        m = loops.SLOPE_M
        with mock.patch.object(loops, "outer_loops", return_value=outer):
            plan = loops.plan(b"")
        self.assertEqual([(name, k, ns, nests) for name, k, ns, nests in plan],
                         [("base", None, [1, 1], [1, 1]), ("m", 0, [m, 1], [1, 1]), ("m2", 0, [2 * m, 1], [1, 1]),
                          ("m4", 0, [4 * m, 1], [1, 1]), ("word", 0, [2 * m, 1], [2, 1]), ("zero", 1, [1, 0], [1, 1]),
                          ("m", 1, [1, m], [1, 1]), ("m2", 1, [1, 2 * m], [1, 1]), ("m4", 1, [1, 4 * m], [1, 1])])
        pr = loops.profile(plan, [{"x": float(v)} for v in range(9)])
        self.assertEqual(pr["loops"], [{"exit": "body", "nested": True, "at": m, "zero": None, "m": {"x": 1.0},
                                        "m2": {"x": 2.0}, "m4": {"x": 3.0}, "word": {"x": 4.0}},
                                       {"exit": "header", "nested": False, "at": m, "zero": {"x": 5.0},
                                        "m": {"x": 6.0}, "m2": {"x": 7.0}, "m4": {"x": 8.0}, "word": None}])
        self.assertEqual((pr["zero"], pr["model"]), (None, loops.MODEL))

    def test_every_header_loop_at_zero_is_measured(self):
        # two header-tested loops (each alone at 0 -> 20, 25; both -> 4) and a while(true) loop left at 1
        p = prof(29.0, [line(29.0, 6.0, zero=20.0), line(29.0, 4.0, zero=25.0), line(29.0, 2.0, "body")])
        p["zero"] = {"ls": 4.0}
        self.assertAlmostEqual(loops.cycles_at(p, [0, 0, 1])["ls"], 4.0)
        self.assertAlmostEqual(loops.cycles_at(p, [0, 0, 3])["ls"], 4.0 + 2 * 2.0)
        self.assertAlmostEqual(loops.cycles_at(p, [0, 1, 1])["ls"], 20.0)
        self.assertAlmostEqual(loops.cycles_at(p, [1, 2, 0])["ls"], 29.0 + 4.0 - 2.0)
        self.assertAlmostEqual(loops.cycles_at(p, [0.5, 0, 1])["ls"],
                               29.0 - 0.5 * 9.0 - 4.0 + 0.5 * (4.0 - 29.0 + 9.0 + 4.0))

class LoopCounterTest(unittest.TestCase):
    """rd_loop_counter: the fragment shader rd_draws.py swaps in to read trip counts as colour."""

    def counter(self):
        import importlib.util
        import paretogpu
        path = os.path.join(os.path.dirname(paretogpu.__file__), "adapters", "renderdoc", "rd_loop_counter.py")
        spec = importlib.util.spec_from_file_location("rd_loop_counter", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_counting_module_is_valid(self):
        lc = self.counter()
        data = read(LIGHTS_SPV, True)
        conts, nested = lc.outer_loops(data)
        self.assertEqual(len(conts), len(loops.force_spirv(data, 2, describe=True)))
        for part in (conts[:2], conts[2:]):
            if not part:
                continue
            out = lc.counting(data, part, 1.0)
            self.assertGreater(struct.unpack_from("<I", out, 12)[0], struct.unpack_from("<I", data, 12)[0])
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
        self.assertEqual(self.counter().outer_loops(read(STATIC_SPV, True)), ([], []))


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

    def test_forced_cluster_module(self):
        src = read(CLUSTER_SPV, True)
        for _, _, ns, nests in loops.plan(src):
            self.assertValid(loops.force_spirv(src, ns, nests))

    def assertValid(self, out):
        self.assertIsNotNone(out)
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

    @unittest.skipUnless(os.path.exists(mali.MALIOC), "malioc not installed")
    def test_price_is_linear_in_n(self):
        src = read(SPV, True)
        p = rules.parametric(src, "vulkan", "fragment", "Mali-G78")
        self.assertIsNotNone(p)
        self.assertEqual(len(p["loops"]), 1)
        lp = p["loops"][0]
        self.assertEqual(lp["exit"], "header")
        self.assertIsNone(lp["word"])  # no nested loops
        c0, c1, c4 = (loops.cycles_at(p, n)["sfu"] for n in (0, 1, 4))
        self.assertEqual(c0, lp["zero"]["sfu"])
        self.assertLess(c0, c1)
        self.assertAlmostEqual(c4 - c1, 3 * (c1 - c0), delta=0.25 * 3 * (c1 - c0))

    @unittest.skipUnless(os.path.exists(mali.MALIOC), "malioc not installed")
    def test_slope_without_unrolling(self):
        # wave loop (2): at a few constant iterations malioc keeps _Waves[j] in registers (no LS per iteration), a
        # dynamic loop loads it every iteration
        src = read(CLUSTER_SPV, True)

        def direct(ns, core):
            return pricing.combined(mali.measure(loops.force_spirv(src, ns), core, "vulkan", "fragment"))["longest"]
        for core in ("Mali-G78", "Mali-G52", "Mali-G720"):
            p = rules.parametric(src, "vulkan", "fragment", core)
            self.assertEqual([lp["exit"] for lp in p["loops"]], ["header", "body", "header"])
            self.assertEqual(direct([1, 1, 3], core)["ls"], direct([1, 1, 1], core)["ls"], core)  # unrolled
            got = loops.cycles_at(p, [1, 1, 3])["ls"] - loops.cycles_at(p, [1, 1, 1])["ls"]
            self.assertAlmostEqual(got, 2.0, delta=0.1, msg=core)
            for n in (96, 200):
                self.assertAlmostEqual(loops.cycles_at(p, [1, 1, n])["ls"], direct([1, 1, n], core)["ls"], delta=1.5,
                                       msg=f"{core} n={n}")

    @unittest.skipUnless(os.path.exists(mali.MALIOC), "malioc not installed")
    def test_loop_with_a_constant_cap(self):
        # leaves at 24 steps too: forced past 24 the price stops growing, the ladder goes down until it grows again
        src = read(CAPPED_SPV, True)
        for core in ("Mali-G78", "Mali-G52", "Mali-G720"):
            p = rules.parametric(src, "vulkan", "fragment", core)
            self.assertLess(p["loops"][0]["at"] * 4, 24 + 1, core)
            for n in (2, 12, 24):
                direct = pricing.combined(mali.measure(loops.force_spirv(src, n), core, "vulkan", "fragment"))
                self.assertAlmostEqual(loops.cycles_at(p, n)["t"], direct["longest"]["t"], delta=0.05, msg=core)

    @unittest.skipUnless(os.path.exists(mali.MALIOC), "malioc not installed")
    def test_while_true_keeps_its_scan_at_zero(self):
        # forced to 0 iterations the light loop loses its tile read; the real one reads the tile and then exits
        src = read(CLUSTER_SPV, True)
        p = rules.parametric(src, "vulkan", "fragment", "Mali-G78")
        forced0 = pricing.combined(mali.measure(loops.force_spirv(src, [1, 0, 1]), "Mali-G78", "vulkan",
                                                "fragment"))["longest"]
        one, s = loops._line(p["loops"][1], "ls")
        self.assertAlmostEqual(loops.cycles_at(p, [1, 0, 1])["ls"], loops.cycles_at(p, 1)["ls"] - s)
        self.assertGreater(loops.cycles_at(p, [1, 0, 1])["ls"], forced0.get("ls") or 0)


class SpirvBoundsTest(unittest.TestCase):
    def test_unity_light_loop(self):
        data = read(LIGHTS_SPV, True)
        b = loops.spirv_bounds(data)
        self.assertEqual(len(b), len(loops.force_spirv(data, 2, describe=True)))
        self.assertEqual(b[0], ["int", ["min", ["b", 1, 0, 16, "f"], ["b", 1, 2, 180, "f"]]])
        self.assertIsNone(b[1])  # its bound is stored in a branch: not known before the loop
        blocks = {"@1.0": base64.b64encode(struct.pack("<8f", 0, 9, 0, 0, 3.7, 0, 0, 0)).decode(),
                  "@1.2": base64.b64encode(bytes(176) + struct.pack("<4f", 1, 2, 0, 0)).decode()}
        self.assertEqual(loops.bound_value(b[0], blocks), 2.0)  # int(min(3.7, 2))
        self.assertIsNone(loops.bound_value(b[0], {"@1.0": blocks["@1.0"]}))  # a block the call did not read
        self.assertIsNone(loops.bound_value(["b", 1, 0, 64, "f"], blocks))  # past the bytes of the block
        self.assertEqual(loops.bound_text(b[0]), "int(min(block 1.0 +16 (f), block 1.2 +180 (f)))")

    def test_vector_written_by_shuffle(self):
        # u_xlatu1.xw = uvec2(CR_Params0.zw): the bound min(u_xlatu1.w, 128) is read through OpVectorShuffle
        b = loops.spirv_bounds(read(CLUSTER_SPV, True))
        self.assertEqual(b, [["min", ["int", ["b", 1, 0, 12, "f"]], ["c", 128.0]], None, ["b", 1, 0, 16, "u"]])
        block = {"@1.0": base64.b64encode(struct.pack("<4f4I", 0, 0, 7, 3.0, 5, 0, 0, 0)).decode()}
        self.assertEqual([loops.bound_value(x, block) for x in b], [3.0, None, 5.0])

    def test_glslang_counter(self):
        self.assertEqual(loops.spirv_bounds(read(SPV, True)), [["b", 0, 0, 0, "i"]])
        self.assertIsNone(loops.spirv_bounds(read(STATIC_SPV, True)))  # no dynamic loop


if __name__ == "__main__":
    unittest.main()
