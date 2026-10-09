"""adapters/unity/bridge.py: the C# file Unity compiles, and the job protocol (answer, progress, error with a code)
with a fake editor in place of Unity CLI.

Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from paretogpu.adapters.unity import bridge
from paretogpu.adapters.unity.cli import CS, UnityError


class Failed(UnityError):
    code = "failed"


class Editor:
    """In place of bridge.run: the entry reads its config and acts like a cs/ script."""

    def __init__(self, act, started=False):
        self.act, self.started = act, started

    def __call__(self, project, script, entry, args, error, timeout=120):
        with open(args[0], encoding="utf-8") as f:
            cfg = json.load(f)
        path = lambda ext: os.path.join(cfg["out"], cfg["name"] + ext)
        if self.started:
            threading.Thread(target=self.act, args=(cfg, path)).start()
            return "started"
        return self.act(cfg, path)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def call(self, editor, **kw):
        with mock.patch.object(bridge, "run", editor):
            return bridge.call("P", "ParetoGpuFrame.cs", "Start", self.out, "job", {"n": 3}, Failed, **kw)

    def test_source_has_the_job_class_and_usings_first(self):
        path = bridge.source("ParetoGpuVariants.cs")
        self.assertEqual(os.path.basename(path), "ParetoGpuVariants.cs")
        self.assertEqual(path, bridge.source("ParetoGpuVariants.cs"))
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        usings = [i for i, x in enumerate(lines) if bridge.USING.match(x)]
        self.assertEqual(usings, list(range(len(usings))))
        self.assertEqual(len(set(lines[i] for i in usings)), len(usings))
        text = "\n".join(lines)
        self.assertIn("public static class ParetoGpuJob", text)
        self.assertIn("public static class ParetoGpuVariants", text)

    def test_every_script_uses_the_job(self):
        for fn in os.listdir(CS):
            if fn.endswith(".cs") and fn != bridge.JOB:
                with open(os.path.join(CS, fn), encoding="utf-8") as f:
                    self.assertIn("ParetoGpuJob.Open(", f.read(), fn)

    def test_answer_and_config(self):
        def act(cfg, path):
            self.assertEqual(cfg["n"], 3)
            write(path(".progress"), "half")
            write(path(".json"), json.dumps({"x": cfg["n"]}))
            return "ok"

        seen = []
        self.assertEqual(self.call(Editor(act), on_progress=seen.append), {"x": 3})
        self.assertEqual(seen, ["half"])

    def test_started_waits_for_the_answer(self):
        def act(cfg, path):
            time.sleep(0.3)
            write(path(".json"), "[1]")

        self.assertEqual(self.call(Editor(act, started=True)), [1])

    def test_error_with_a_code(self):
        def act(cfg, path):
            write(path(".error"), "[no_frame] the Frame Debugger did not capture a frame")
            return "error"

        with self.assertRaises(Failed) as e:
            self.call(Editor(act))
        self.assertEqual(e.exception.code, "no_frame")
        self.assertEqual(str(e.exception), "the Frame Debugger did not capture a frame")

    def test_error_without_a_code_is_the_callers(self):
        def act(cfg, path):
            write(path(".error"), "[error] System.Exception: boom")

        with self.assertRaises(Failed) as e:
            self.call(Editor(act, started=True))
        self.assertEqual(e.exception.code, "failed")

    def test_old_files_do_not_pass_for_the_answer(self):
        write(os.path.join(self.out, "job.json"), "{}")
        with self.assertRaises(Failed):
            self.call(Editor(lambda cfg, path: "ok"))

    def test_no_answer_in_time(self):
        with self.assertRaises(Failed):
            self.call(Editor(lambda cfg, path: None, started=True), timeout=1)


if __name__ == "__main__":
    unittest.main()
