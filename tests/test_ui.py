"""ui/presets.py and ui/runner.py: steps, folders and command lines a button runs; failed-run hints."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from paretogpu.store import workspace
from paretogpu.ui import api, presets, runner

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
        self.assertEqual(presets.phases_of("frame", frame), ["rd_capture", "snapshot", "rd_counters", "rd_draws"])

    def test_frame_cost_of_a_phone_is_a_device_snapshot_priced_like_the_editors(self):
        with mock.patch.object(presets, "android_package", return_value="com.game"):
            steps, frame_dir = presets.plan("frame_cost", {"project": r"device:SER1:D:\Game", "suffix": "x",
                                                           "cores": "Mali-G78", "wait": 5}, SCH)
        self.assertEqual([c for c, _ in steps], ["device", "cost"])
        self.assertEqual(steps[0][1]["wait"], 5)
        self.assertTrue(frame_dir.endswith("_x_device"))
        dev, cost = steps[0][1], steps[1][1]
        self.assertEqual((dev["frame"], dev["project"], dev["app"], dev["serial"]), (frame_dir, r"D:\Game", "com.game",
                                                                                      "SER1"))
        self.assertEqual((cost["frame"], cost["project"]), (frame_dir, r"D:\Game"))
        self.assertEqual(cost["variants"], workspace.variants_dir(r"D:\Game"))

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
        with self.assertRaises(ValueError):
            presets.plan("nothing", {}, SCH)

    def test_bench_steps(self):
        steps, _ = presets.plan("site", {"gpus": "Mali-G78"}, SCH)
        self.assertEqual([c for c, _ in steps], ["bench"])
        self.assertEqual(presets.argv_of("bench", steps[0][1], SCH)[3:], ["paretogpu", "bench", "--gpus", "Mali-G78"])
        self.assertEqual(presets.phases_of("bench", {}), ["bench_compile", "site"])


class LabelsTest(unittest.TestCase):
    def test_every_argument_has_a_label(self):
        for c in presets.COMMANDS:
            for a, _ in c.flat_args():
                self.assertTrue(a.label, f"{c.name} {a.flag}")


class HintTest(unittest.TestCase):
    def job(self, log, codes=(), cmd="cost"):
        return {"log": log, "error": None, "steps": [{"cmd": cmd, **({"error_code": c} if c else {})} for c in
                                                    (list(codes) or [None])]}

    def test_by_code_then_by_text(self):
        h = runner.hint_of(self.job(["x"], ["editor_busy"]))
        self.assertIn("Unity", h["text"])
        self.assertIn("RenderDoc", runner.hint_of(self.job(["renderdoc failed"], cmd="frame"))["text"])
        self.assertIn("лог", runner.hint_of(self.job(["something else"]))["text"])

    def test_pipeline_asset_hint_names_both_assets(self):
        job = self.job(["the frame was drawn with pipeline asset ..."], ["pipeline_asset"])
        job["steps"][0]["error_details"] = {"frame": "Assets/K31.asset", "editor": "Assets/PC.asset",
                                            "changed": False, "variants": 6}
        text = runner.hint_of(job)["text"]
        for part in ("Assets/K31.asset", "Assets/PC.asset", "Play Mode", "пересчитайте", "6"):
            self.assertIn(part, text)
        job["steps"][0]["error_details"]["changed"] = True
        self.assertIn("изменён после снимка", runner.hint_of(job)["text"])
        self.assertIn("лог", runner.hint_of(self.job(["pipeline asset"]))["text"])  # no details: no names to give

    def test_renderdoc_failing_on_the_phone_gets_the_phone_hint(self):
        self.assertIn("телефона", runner.hint_of(self.job(["x"], ["renderdoc"], cmd="device"))["text"])
        self.assertIn("Load RenderDoc", runner.hint_of(self.job(["x"], ["renderdoc"], cmd="frame"))["text"])


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = mock.patch.object(api, "STATE", self.dir)
        self.state.start()

    def tearDown(self):
        self.state.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_remembered_projects_are_unique_folders_without_phones(self):
        api.remember_values("frame_cost", {"project": "D:/Game/"})
        api.remember_values("frame_cost", {"project": r"d:\game"})
        api.remember_values("frame_cost", {"project": r"device:SER1:D:\Other"})
        with open(os.path.join(self.dir, "settings.json"), encoding="utf-8") as f:
            projects = json.load(f)["projects"]
        self.assertEqual([api.project_key(p) for p in projects], [api.project_key(r"D:\Other"),
                                                                   api.project_key(r"D:\Game")])

    def test_adb_answers_are_cached(self):
        api._adb.clear()
        with mock.patch("paretogpu.adapters.renderdoc.devices", return_value=[("S", "Phone")]) as dev:
            self.assertEqual(api.adb_cached("devices"), [("S", "Phone")])
            api.adb_cached("devices")
        self.assertEqual(dev.call_count, 1)
        api._adb.clear()


if __name__ == "__main__":
    unittest.main()
