"""ui/presets.py and ui/runner.py: what a button runs (steps, their folders and command lines) and the hint of a failed
run; synthetic values, a temporary snapshot folder.

Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

from paretogpu.store import workspace
from paretogpu.ui import presets, runner

SCH = presets.schema()


class PresetsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_frame_cost_chains_the_snapshot_into_its_cost(self):
        steps, frame_dir = presets.plan("frame_cost", {"project": r"D:\Game", "suffix": "t 1", "cores": "Mali-G78",
                                                       "no_compile": True}, SCH)
        self.assertEqual([c for c, _ in steps], ["frame", "cost"])
        self.assertTrue(os.path.basename(frame_dir).startswith("frame_Game_") and frame_dir.endswith("_t_1"))
        frame, cost = steps[0][1], steps[1][1]
        self.assertEqual(frame, {"project": r"D:\Game", "suffix": "t 1", "out": frame_dir})
        self.assertEqual(cost["frame"], frame_dir)
        self.assertEqual(cost["project"], r"D:\Game")
        self.assertEqual(cost["variants"], workspace.variants_dir(r"D:\Game"))
        self.assertTrue(cost["no_compile"])
        self.assertEqual(presets.report_of("cost", cost), os.path.join(frame_dir, "frame_report.html"))
        self.assertEqual(presets.phases_of("frame", frame), ["rd_capture", "snapshot", "rd_counters"])

    def test_cost_of_a_snapshot_uses_its_projects_variants(self):
        with open(os.path.join(self.dir, "frame_events.json"), "w", encoding="utf-8") as f:
            json.dump({"project": r"D:\Other", "events": []}, f)
        steps, _ = presets.plan("cost", {"frame": self.dir, "materials": True}, SCH)
        self.assertEqual(steps[0][1]["variants"], workspace.variants_dir(r"D:\Other"))
        self.assertEqual(presets.phases_of("cost", steps[0][1])[0], "materials")

    def test_material_analyses_get_their_own_folder(self):
        steps, _ = presets.plan("matcompare", {"project": r"D:\Game", "a": "A.mat", "b": "B.mat"}, SCH)
        v = steps[0][1]
        self.assertEqual(os.path.dirname(v["out"]), workspace.results_dir("matcompare"))
        self.assertEqual(presets.report_of("matcompare", v), os.path.join(v["out"], "matcompare.html"))
        argv = presets.argv_of("matcompare", v, SCH)
        self.assertEqual(argv[:5], [sys.executable, "-u", "-m", "paretogpu", "matcompare"])
        self.assertIn("A.mat", argv)
        self.assertEqual(argv[argv.index("--project") + 1], r"D:\Game")

    def test_missing_values_are_refused(self):
        with self.assertRaises(ValueError):
            presets.plan("frame_cost", {}, SCH)
        with self.assertRaisesRegex(ValueError, "matshader: .*material"):
            presets.plan("matshader", {"project": r"D:\Game"}, SCH)
        with self.assertRaises(ValueError):
            presets.plan("nothing", {}, SCH)

    def test_bench_steps(self):
        steps, _ = presets.plan("site", {"gpus": "Mali-G78"}, SCH)
        self.assertEqual([c for c, _ in steps], ["bench_run", "bench_site"])
        self.assertTrue(presets.argv_of("bench_run", steps[0][1], SCH)[2].endswith(os.path.join("bench", "run.py")))
        self.assertEqual(presets.phases_of("bench_run", {}), ["bench_compile"])


class HintTest(unittest.TestCase):
    def job(self, log, codes=(), cmd="cost"):
        return {"log": log, "error": None, "steps": [{"cmd": cmd, **({"error_code": c} if c else {})} for c in
                                                    (list(codes) or [None])]}

    def test_by_code_then_by_text(self):
        h = runner.hint_of(self.job(["x"], ["editor_busy"]))
        self.assertIn("Unity", h["text"])
        self.assertEqual(h["action"], "no_compile")
        self.assertIn("RenderDoc", runner.hint_of(self.job(["renderdoc failed"], cmd="frame"))["text"])
        self.assertIsNone(runner.hint_of(self.job(["does not answer Unity CLI or is busy"], cmd="frame"))["action"])
        self.assertIn("лог", runner.hint_of(self.job(["something else"]))["text"])


if __name__ == "__main__":
    unittest.main()
