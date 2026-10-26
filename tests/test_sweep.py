"""core/sweep: GPU bursts of a Streamline timeline matched to rd_sweep.py's replays."""
import unittest

from paretogpu.core import sweep
from paretogpu.model.errors import ParetoError

HEAD = ["Index (s)", "Mali GPU Cycles:GPU active", "Mali Shader Core Cycles:Execution core active",
        "Mali ALU Instructions:FMA pipe instructions", "Mali Texture Unit Cycles:Filtering active",
        "Mali ALU Instructions:CVT pipe instructions", "Mali ALU Instructions:SFU pipe instructions",
        "Mali Load/Store Unit Cycles:Full reads", "Mali Load/Store Unit Cycles:Partial reads",
        "Mali Load/Store Unit Cycles:Full writes", "Mali Load/Store Unit Cycles:Partial writes",
        "Mali Varying Unit Requests:16-bit interpolation slots", "Mali Varying Unit Requests:32-bit interpolation slots"]


def timeline(bursts, length=6.0, extra=(0.0,) * 8):
    """bursts [(start s, exec per ms, fma per ms, ms)] -> rows of 1 ms; extra: CVT .. 32-bit slots per busy ms."""
    rows = [[k / 1000] + [0.0] * (len(HEAD) - 1) for k in range(int(length * 1000))]
    for start, ex, fma, ms in bursts:
        for k in range(int(start * 1000), int(start * 1000) + ms):
            rows[k][1:] = [1.0, ex, fma, 0.0, *extra]
    return rows


class SweepTest(unittest.TestCase):
    def test_bursts_split_on_idle(self):
        b = sweep.bursts(HEAD, timeline([(0.1, 10, 1, 50), (0.2, 10, 1, 50), (1.0, 5, 1, 10)]))
        self.assertEqual([(round(s, 3), v[0]) for s, v in b], [(0.1, 1000.0), (1.0, 50.0)])  # 0.15 s idle splits

    def test_missing_counter_is_named(self):
        with self.assertRaisesRegex(ParetoError, "Partial writes"):
            sweep.bursts(HEAD[:10] + HEAD[11:], timeline([(0.1, 10, 1, 50)]))

    def test_units_as_renderdoc_issue_cycles(self):
        (_, v), = sweep.bursts(HEAD, timeline([(0.1, 10, 4, 10)], extra=(3, 6, 1, 1, 1, 1, 2, 5)))
        named = dict(zip(sweep.NAMES, v))
        self.assertEqual(named["Arithmetic unit issue cycles"], 60.0)  # max(FMA 40, CVT 30, SFU 60), not 130
        self.assertEqual(named["Load/store unit issue cycles"], 40.0)
        self.assertEqual(named["Varying unit issue cycles"], 45.0)  # 16-bit slots + half the raw 32-bit ones

    def test_replays_matched_by_time(self):
        # warm-up at 0.5, base at 1.5 / 2.0, shader 0 at 3.5 / 4.0, an unlogged swap replay at 3.0, base at 5.0
        rows = timeline([(0.5, 30, 3, 50), (1.5, 20, 3, 50), (2.0, 22, 3, 50), (3.0, 20, 3, 50), (3.5, 19, 1, 50),
                         (4.0, 19, 1, 50), (5.0, 21, 3, 50)])
        t0 = 100.0  # the replays' wall clock; the burst starts 0.05 s after each mark
        marks = [[-2, t0 + 0.45], [-1, t0 + 1.45], [-1, t0 + 1.95], [0, t0 + 3.45], [0, t0 + 3.95], [-1, t0 + 4.95]]
        jobs = [{"stage": "frag", "mode": "colour", "file": "a.frag.spv", "events": [7]},
                {"stage": "frag", "mode": "colour", "file": "b.frag.spv", "events": [8], "error": "no_output"}]
        k = sweep.knockout({"jobs": jobs, "marks": marks}, HEAD, rows)
        self.assertEqual(k["source"], "streamline")
        self.assertEqual(k["base"]["frame"][:2], [1000.0, 150.0])  # least of 1000, 1100, 1050: other GPU work only adds
        self.assertEqual(k["spread"]["frame"], 100.0)
        self.assertEqual(k["shaders"][0]["passes"]["frame"][:2], [950.0, 50.0])
        self.assertEqual(k["shaders"][1], {"stage": "frag", "mode": "colour", "file": "b.frag.spv", "events": [8],
                                           "error": "no_output"})

    def test_late_warm_up(self):
        # Pixel 7: the warm-up's burst 2.1 s after its mark, every other replay at once
        rows = timeline([(2.6, 30, 3, 50), (4.0, 20, 3, 50), (4.5, 20, 3, 50), (6.0, 20, 3, 50), (6.5, 19, 1, 50),
                         (7.0, 19, 1, 50)], length=8.0)
        t0 = 10.0
        marks = [[-2, t0 + 0.5], [-1, t0 + 4.0], [-1, t0 + 4.5], [0, t0 + 6.5], [0, t0 + 7.0]]
        k = sweep.knockout({"jobs": [{"stage": "frag", "mode": "colour", "file": "a", "events": [1]}], "marks": marks},
                           HEAD, rows)
        self.assertEqual(k["base"]["frame"][:2], [1000.0, 150.0])
        self.assertEqual(k["shaders"][0]["passes"]["frame"][:2], [950.0, 50.0])

    def test_partial_replays_dropped(self):
        rows = timeline([(0.5, 30, 3, 50), (1.5, 20, 3, 50), (2.0, 20, 3, 50), (2.5, 20, 3, 2), (3.5, 19, 1, 50)])
        t0 = 10.0
        marks = [[-2, t0 + 0.45], [-1, t0 + 1.45], [-1, t0 + 1.95], [-1, t0 + 2.45], [0, t0 + 3.45]]
        k = sweep.knockout({"jobs": [{"stage": "frag", "mode": "colour", "file": "a", "events": [1]}], "marks": marks},
                           HEAD, rows)
        self.assertEqual((k["base"]["frame"][0], k["spread"]["frame"]), (1000.0, 0.0))  # the 40-cycle sliver is out

    def test_nothing_seen(self):
        self.assertEqual(sweep.knockout({"jobs": [], "marks": [[-2, 1.0]]}, HEAD, timeline([])), {"error": "no_bursts"})


if __name__ == "__main__":
    unittest.main()
