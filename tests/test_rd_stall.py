"""adapters/renderdoc._finish: a script stuck in a step that may hang is stopped, its answer so far is used."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from paretogpu.adapters import renderdoc as rdoc


class StallTest(unittest.TestCase):
    def run_stuck(self, step, partial=True):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "a.json")
        with open(out + ".progress", "w", encoding="utf-8") as f:
            f.write(step)
        if partial:
            with open(out + ".partial", "w", encoding="utf-8") as f:
                json.dump({"mali": {"error": "hung"}}, f)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        log = open(out + ".log", "w", encoding="utf-8")
        called = []

        def then():
            called.append(1)
            if os.path.exists(out + ".partial"):
                os.replace(out + ".partial", out)
        t0 = time.time()
        try:
            res = rdoc._finish((proc, log, out, "device"), 30, stall=(("Mali counters",), 1, then))
        finally:
            if proc.poll() is None:
                proc.kill()
        return res, called, time.time() - t0

    def test_stuck_counters_use_the_answer_so_far(self):
        res, called, took = self.run_stuck("Mali counters of the render passes")
        self.assertEqual((res, called), ({"mali": {"error": "hung"}}, [1]))
        self.assertLess(took, 10)

    def test_other_steps_are_not_cut(self):
        with self.assertRaises(rdoc.RenderDocError):  # the timeout, not the stall, ends it (shortened here)
            d = tempfile.mkdtemp()
            out = os.path.join(d, "a.json")
            with open(out + ".progress", "w", encoding="utf-8") as f:
                f.write("draws: round 1/1")
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            try:
                rdoc._finish((proc, open(out + ".log", "w"), out, "device"), 3,
                             stall=(("Mali counters",), 1, lambda: None))
            finally:
                if proc.poll() is None:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
