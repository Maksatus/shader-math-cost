"""Frame cost (core/frame_cost.py) and the pixel source rule on a synthetic frame."""
import base64
import struct
import unittest

from paretogpu.core import frame_cost as cost
from paretogpu.core import loops
from paretogpu.core import pixels
from paretogpu.model import variant as variants


def event(index, stage, shader, pixels_, vertices=4, meshes=(), rd=None, kws=()):
    return {"index": index, "kind": "draw", "type": "Draw", "stage": stage, "path": f"Camera/{shader}",
            "object": None, "mesh": None, "meshes": list(meshes), "shader": shader, "pass": "P", "subshader": 0,
            "pass_index": 0, "keywords": list(kws), "vertices": vertices,
            "rt": {"name": "Color", "width": 100, "height": 100},
            "rd": rd or {"ps_invocations": pixels_}}


def record(file, stage, cycles, core="Mali-G78"):
    c = {"arith": cycles, "fma": cycles, "cvt": 0.0, "sfu": 0.0, "ls": 0.0, "v": 0.0, "t": 0.0}
    v = {"longest": c, "shortest": c, "total": c, "bound": ["fma"], "work_regs": 16, "spilling": False,
         "fp16_pct": 50, "occupancy": 100}
    vs = {"main": v} if stage in ("fragment", "compute") else {"position": v, "varying": dict(v, longest={**c, "fma": 0.0, "arith": 0.0})}
    return {"file": file, "core": core, "arch": "Valhall", "stage": stage, "api": "vulkan", "malioc": "x",
            "driver": "x", "variants": vs, "uniform_computation": False, "source_sha1": file}


def state(events, prices):
    """variants.run() state with fragment / vertex prices per shader."""
    st = {"keys": [], "files": {}, "errors": {}, "measurements": {}}
    for ev in events:
        k = variants.VariantKey.of(ev)
        fp, vp = prices[ev["shader"]]
        st["keys"].append(k)
        st["files"][k] = {"frag": f"{ev['shader']}.frag.spv", "vert": f"{ev['shader']}.vert.spv"}
        st["errors"][k] = []
        st["measurements"][f"{ev['shader']}.frag.spv"] = {"Mali-G78": record(f"{ev['shader']}.frag.spv", "fragment", fp)}
        st["measurements"][f"{ev['shader']}.vert.spv"] = {"Mali-G78": record(f"{ev['shader']}.vert.spv", "vertex", vp)}
    return st


class CostTest(unittest.TestCase):
    def test_cheap_fullscreen_beats_expensive_small(self):
        evs = [event(0, "opaque", "Small", 200, vertices=300),
               event(1, "post", "Fullscreen", 10000, vertices=3),
               event(2, "opaque", "Mid", 1000, vertices=1000)]
        c = cost.compute({}, evs, state(evs, {"Small": (500, 10), "Fullscreen": (20, 4), "Mid": (30, 8)}))
        rows = c["events"]
        self.assertEqual([r["shader"] for r in rows], ["Fullscreen", "Small", "Mid"])
        x = rows[0]["cost"]["Mali-G78"]
        self.assertEqual(x["fragment"], 10000 * 20)
        self.assertEqual(x["vertex"], 3 * 4)
        self.assertAlmostEqual(sum(r["cost"]["Mali-G78"]["share"] for r in rows), 1.0)
        for g in cost.GROUPS:
            self.assertAlmostEqual(sum(a["share"] for a in c["groups"]["Mali-G78"][g]), 1.0)
        self.assertEqual(c["coverage"], {"draws": 3, "draws_priced": 3, "events": 3, "events_priced": 3})

    def test_loop_bounded_by_a_uniform_runs_as_the_event_says(self):
        ev = event(0, "opaque", "L", 100, vertices=3)
        ev["params"] = {"@0.1": base64.b64encode(struct.pack("<4f", 3.0, 9.0, 0.0, 0.0)).decode()}
        st = state([ev], {"L": (10, 1)})
        m = loops.SLOPE_M

        def line(zero, s):  # header-tested loop, once = base, s per iteration
            a = 10.0 + (m - 1) * s
            return {"exit": "header", "nested": False, "at": m, "zero": {"fma": zero, "arith": zero},
                    **{k: {"fma": a + i * m * s, "arith": a + i * m * s} for k, i in (("m", 0), ("m2", 1), ("m4", 3))},
                    "word": None}
        prof = {"base": {"fma": 10.0, "arith": 10.0}, "zero": None, "loops": [line(6.0, 3.0), line(8.0, 2.0)]}
        st["loops"] = {"L.frag.spv": {"Mali-G78": prof}}
        st["loop_bounds"] = {"L.frag.spv": [None, ["min", ["b", 0, 1, 0, "f"], ["c", 8.0]]]}
        c = cost.compute({}, [ev], st)
        r = c["events"][0]
        self.assertEqual(r["cost"]["Mali-G78"]["px_price"], 10.0 + 1 * 3.0 + 2 * 2.0)  # n = [2 (default), 3 (block 0.1 +0)]
        self.assertEqual([(x["n"], x["from"]) for x in r["loop"]["loops"]], [(2, "default"), (3, "uniform")])
        self.assertEqual(r["loop"]["loops"][1]["bound"], "min(block 0.1 +0 (f), 8)")
        self.assertEqual(c["loops"]["bounded"], 1)
        # capture gives loop 0: 1.5 iterations on average
        st["loop_iters"] = {0: {"n": [1.5, 0.0], "pixels": 16, "calls": 1}}
        c = cost.compute({}, [ev], st)
        r = c["events"][0]
        self.assertEqual([(x["n"], x["from"]) for x in r["loop"]["loops"]], [(1.5, "frame"), (3, "uniform")])
        self.assertEqual(r["cost"]["Mali-G78"]["px_price"], 10.0 + 0.5 * 3.0 + 2 * 2.0)
        self.assertEqual(c["loops"]["unknown"], 0)
        # `i < 2.5` runs 3 times
        ev["params"] = {"@0.1": base64.b64encode(struct.pack("<4f", 2.5, 9.0, 0.0, 0.0)).decode()}
        r = cost.compute({}, [ev], st)["events"][0]
        self.assertEqual([x["n"] for x in r["loop"]["loops"]], [1.5, 3])

    def test_renderdoc_vertices_and_compute(self):
        draw = event(0, "opaque", "A", 100, vertices=50, rd={"ps_invocations": 100, "vs_invocations": 70})
        disp = {"index": 1, "kind": "compute", "type": "ComputeDispatch", "stage": "compute", "path": "X/Scatter",
                "object": None, "mesh": None, "meshes": [], "shader": None, "pass": None, "keywords": [], "vertices": 0,
                "rt": {"name": "Color", "width": 100, "height": 100}, "pixel_count": 0, "pixel_method": None,
                "compute": {"shader": "CS", "kernel": "K", "groups": [10, 2, 1], "group_size": [8, 8, 1]}}
        st = state([draw], {"A": (10, 2)})
        k = variants.VariantKey.of(disp)
        st["files"][k] = {"comp": "CS.comp.spv"}
        st["measurements"]["CS.comp.spv"] = {"Mali-G78": record("CS.comp.spv", "compute", 3)}
        c = cost.compute({}, [draw, disp], st)
        a, b = sorted(c["events"], key=lambda r: r["index"])
        self.assertEqual((a["vertices"], a["vertex_method"]), (70, "renderdoc"))
        self.assertEqual(a["cost"]["Mali-G78"]["vertex"], 70 * 2)
        self.assertEqual((b["threads"], b["thread_method"]), (10 * 2 * 8 * 8, "dispatch"))
        self.assertEqual(b["cost"]["Mali-G78"]["total"], 1280 * 3)
        self.assertEqual(c["totals"]["Mali-G78"]["compute"], 1280 * 3)
        self.assertAlmostEqual(sum(r["cost"]["Mali-G78"]["share"] for r in c["events"]), 1.0)
        disp["rd"] = {"cs_invocations": 999}
        self.assertEqual(cost.threads(disp), (999, "renderdoc"))

    def test_unmeasured_event_is_listed(self):
        evs = [event(0, "opaque", "A", 100)]
        st = state(evs, {"A": (10, 1)})
        st["measurements"].pop("A.frag.spv")
        c = cost.compute({}, evs, st)
        self.assertEqual(c["coverage"]["draws_priced"], 0)
        self.assertIn("fragment", c["missing"][0]["reason"])


class PixelsTest(unittest.TestCase):
    def test_renderdoc_or_none(self):
        evs = [event(0, "opaque", "S", 777), event(1, "opaque", "S", 0, rd={"ps_invocations": None})]
        p = pixels.resolve(evs)
        self.assertEqual((p[0]["pixels"], p[0]["method"]), (777, "renderdoc"))
        self.assertEqual((p[1]["pixels"], p[1]["method"]), (0, "none"))

    def test_quads_unless_no_fragment_shader_ran(self):
        evs = [event(0, "opaque", "S", 777, rd={"ps_invocations": 777, "threads": 900}),
               event(1, "shadow", "S", 0, rd={"ps_invocations": 0})]  # frame_quads leaves it without threads
        p = pixels.resolve(evs)
        self.assertEqual((p[0]["pixels"], p[0]["method"]), (900, "quads"))
        self.assertEqual(p[1]["pixels"], 0)


class DeviceDrawTest(unittest.TestCase):
    def test_depth_only_draw_has_no_pixel_price(self):
        ev = event(0, "shadow", "S", 0, vertices=24, rd={"ps_invocations": 0, "vs_invocations": 24,
                                                                       "pixel_method": "samples",
                                                                       "vertex_method": "indices"})
        ev["code"] = {"vert": "device/shaders/a.vert.spv", "hash": "abc"}
        st = state([ev], {"S": (10, 3)})
        k = variants.VariantKey.of(ev)
        st["files"][k] = {"vert": "S.vert.spv"}
        st["captured"] = {k}
        c = cost.compute({}, [ev], st)
        r = c["events"][0]
        x = r["cost"]["Mali-G78"]
        self.assertEqual((x["px_price"], x["fragment"], x["vertex"]), (0.0, 0.0, 24 * 3))
        self.assertEqual((r["pixel_method"], r["vertex_method"]), ("samples", "indices"))
        self.assertEqual(c["missing"], [])
        # without "captured" the absent fragment stage counts as missing
        del st["captured"]
        c = cost.compute({}, [ev], st)
        self.assertEqual(c["coverage"]["draws_priced"], 0)


if __name__ == "__main__":
    unittest.main()
