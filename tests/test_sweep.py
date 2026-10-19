"""core/sweep: GPU bursts of a Streamline timeline matched to rd_sweep.py's replays."""
import unittest

from paretogpu.core import sweep

HEAD = ["Index (s)", "Mali GPU Cycles:GPU active", "Mali Shader Core Cycles:Execution core active",
        "Mali ALU Instructions:FMA pipe instructions", "Mali Texture Unit Cycles:Filtering active"]


def timeline(bursts, length=6.0):
    """bursts [(start s, exec per ms, fma per ms, ms)] -> rows of 1 ms."""
    rows = [[k / 1000, 0.0, 0.0, 0.0, 0.0] for k in range(int(length * 1000))]
    for start, ex, fma, ms in bursts:
        for k in range(int(start * 1000), int(start * 1000) + ms):
            rows[k][1:] = [1.0, ex, fma, 0.0]
    return rows


class SweepTest(unittest.TestCase):
    def test_bursts_split_on_idle(self):
        b = sweep.bursts(HEAD, timeline([(0.1, 10, 1, 50), (0.2, 10, 1, 50), (1.0, 5, 1, 10)]))
        self.assertEqual([(round(s, 3), v[0]) for s, v in b], [(0.1, 1000.0), (1.0, 50.0)])  # 0.15 s idle splits

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
