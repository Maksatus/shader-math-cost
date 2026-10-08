"""Frame cost (K1.5) and the pixel source rule on a synthetic frame: no project data.

Run: python -m unittest paretogpu.frame.test_cost   (from the repository root)
"""
import unittest

from paretogpu.frame import cost, pixels, variants


def event(index, stage, shader, pixels_, method, vertices=4, meshes=(), rd=None, kws=()):
    return {"index": index, "kind": "draw", "type": "Draw", "stage": stage, "path": f"Camera/{shader}",
            "object": None, "mesh": None, "meshes": list(meshes), "shader": shader, "pass": "P", "subshader": 0,
            "pass_index": 0, "keywords": list(kws), "vertices": vertices,
            "rt": {"name": "Color", "width": 100, "height": 100},
            "pixel_count": pixels_, "pixel_method": method, **({"rd": rd} if rd else {})}


def record(file, stage, cycles, core="Mali-G78"):
    """A measurement record with `cycles` on the FMA pipe of the longest path."""
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
        k = variants.key_of(ev)
        fp, vp = prices[ev["shader"]]
        st["keys"].append(k)
        st["files"][k] = {("vulkan", "frag"): f"{ev['shader']}.frag.spv", ("vulkan", "vert"): f"{ev['shader']}.vert.spv"}
        st["errors"][k] = []
        st["measurements"][f"{ev['shader']}.frag.spv"] = {"Mali-G78": record(f"{ev['shader']}.frag.spv", "fragment", fp)}
        st["measurements"][f"{ev['shader']}.vert.spv"] = {"Mali-G78": record(f"{ev['shader']}.vert.spv", "vertex", vp)}
    return st


class CostTest(unittest.TestCase):
    def test_cheap_fullscreen_beats_expensive_small(self):
        evs = [event(0, "opaque", "Small", 200, "diff", vertices=300),
               event(1, "post", "Fullscreen", 10000, "fullscreen", vertices=3),
               event(2, "opaque", "Mid", 1000, "diff", vertices=1000)]
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

    def test_renderdoc_vertices_and_compute(self):
        draw = event(0, "opaque", "A", 100, "renderdoc", vertices=50, rd={"ps_invocations": 100, "vs_invocations": 70})
        disp = {"index": 1, "kind": "compute", "type": "ComputeDispatch", "stage": "compute", "path": "X/Scatter",
                "object": None, "mesh": None, "meshes": [], "shader": None, "pass": None, "keywords": [], "vertices": 0,
                "rt": {"name": "Color", "width": 100, "height": 100}, "pixel_count": 0, "pixel_method": None,
                "compute": {"shader": "CS", "kernel": "K", "groups": [10, 2, 1], "group_size": [8, 8, 1]}}
        st = state([draw], {"A": (10, 2)})
        k = variants.key_of(disp)
        st["files"][k] = {("vulkan", "comp"): "CS.comp.spv"}
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
        evs = [event(0, "opaque", "A", 100, "diff")]
        st = state(evs, {"A": (10, 1)})
        st["measurements"].pop("A.frag.spv")
        c = cost.compute({}, evs, st)
        self.assertEqual(c["coverage"]["draws_priced"], 0)
        self.assertIn("fragment", c["missing"][0]["reason"])


class PixelsTest(unittest.TestCase):
    def test_source_priority(self):
        evs = [event(0, "opaque", "S", 500, "diff", meshes=["m"], rd={"ps_invocations": 777}),
               event(1, "opaque", "S", 500, "diff", meshes=["m"]),
               event(2, "transparent", "T", 50, "diff", meshes=["t"]),
               event(3, "post", "F", 10, "fullscreen")]
        p = pixels.resolve(evs)
        self.assertEqual((p[0]["pixels"], p[0]["method"]), (777, "renderdoc"))
        self.assertEqual((p[1]["pixels"], p[1]["method"]), (500, "diff"))
        self.assertEqual((p[2]["pixels"], p[2]["method"]), (50, "diff"))
        self.assertEqual((p[3]["pixels"], p[3]["method"]), (100 * 100, "fullscreen"))


if __name__ == "__main__":
    unittest.main()
