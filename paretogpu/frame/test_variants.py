"""frame/variants.run(): which compiled variants are reused and which are compiled again (fingerprints, deleted
files, failures), with Unity and malioc replaced by stubs; synthetic names, temporary folder.

Run: python -m unittest paretogpu.frame.test_variants   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from paretogpu.frame import variants

EV = {"index": 0, "kind": "draw", "shader": "Test/Lit", "subshader": 0, "pass_index": 0, "pass": "Forward",
      "keywords": ["_A"]}
KEY = variants.key_of(EV)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.compiled = []
        self.fp = {"Test/Lit": "fp1"}
        self.result = {}  # errors of the next compile

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def compile_stub(self, project, keys, root, platforms, timeout=1800):
        self.compiled.append(list(keys))
        result = {"variants": []}
        raw = os.path.join(root, "_compiled")
        os.makedirs(raw, exist_ok=True)
        for i, k in enumerate(keys):
            errs = self.result.get(k, [])
            files = []
            if not errs:
                for s in ("vert", "frag"):
                    fn = f"{i}_vulkan_{s}.bin"
                    with open(os.path.join(raw, fn), "wb") as f:
                        f.write(b"spv")
                    files.append({"platform": "vulkan", "stage": s, "file": fn})
            result["variants"].append({"id": i, "files": files, "errors": errs, "fingerprint": self.fp.get(k[0])})
        return variants.place(root, raw, keys, result)

    def run_once(self, editor=True, **kw):
        with mock.patch.object(variants.unity, "editor_ready", return_value=editor), \
                mock.patch.object(variants, "current_fingerprints", side_effect=lambda p, s, r: dict(self.fp)), \
                mock.patch.object(variants, "compile_variants", side_effect=self.compile_stub), \
                mock.patch.object(variants.measure, "run", return_value=([], [])):
            return variants.run([EV], "project", self.root, ["Mali-G78"], ["vulkan"], progress=lambda m: None, **kw)

    def test_current_variant_is_reused(self):
        self.run_once()
        st = self.run_once()
        self.assertEqual(self.compiled, [[KEY]])
        self.assertTrue(st["checked"])
        self.assertEqual(set(st["files"][KEY]), {("vulkan", "vert"), ("vulkan", "frag")})

    def test_changed_shader_is_compiled_again(self):
        self.run_once()
        self.fp["Test/Lit"] = "fp2"
        self.run_once()
        self.assertEqual(self.compiled, [[KEY], [KEY]])

    def test_recompile(self):
        self.run_once()
        self.run_once(recompile=True)
        self.assertEqual(len(self.compiled), 2)

    def test_deleted_file_is_compiled_again(self):
        st = self.run_once()
        os.remove(os.path.join(self.root, st["files"][KEY][("vulkan", "frag")]))
        self.run_once()
        self.assertEqual(len(self.compiled), 2)

    def test_without_editor_files_are_used_unchecked(self):
        self.run_once()
        self.fp["Test/Lit"] = "fp2"
        st = self.run_once(editor=False, compile_missing=False)
        self.assertEqual(len(self.compiled), 1)
        self.assertFalse(st["checked"])

    def test_failure_is_kept_until_the_shader_changes(self):
        self.result = {KEY: ["vulkan frag: error"]}
        self.run_once()
        st = self.run_once()
        self.assertEqual(len(self.compiled), 1)
        self.assertEqual(st["errors"][KEY], ["vulkan frag: error"])
        self.run_once(retry_failed=True)
        self.assertEqual(len(self.compiled), 2)
        self.fp["Test/Lit"] = "fp2"
        self.run_once()
        self.assertEqual(len(self.compiled), 3)

    def test_unfinished_run_is_not_a_failure(self):
        self.result = {KEY: [variants.NOT_FINISHED]}
        self.run_once()
        self.run_once()
        self.assertEqual(len(self.compiled), 2)

    def test_index_keeps_variants_of_other_frames(self):
        other = {"key": ["Other", 0, 0, "P", []], "files": {}, "errors": ["x"], "fingerprint": "f"}
        with open(os.path.join(self.root, "variants.json"), "w", encoding="utf-8") as f:
            json.dump({"variants": [other]}, f)
        self.run_once()
        with open(os.path.join(self.root, "variants.json"), encoding="utf-8") as f:
            keys = [x["key"][0] for x in json.load(f)["variants"]]
        self.assertEqual(sorted(keys), ["Other", "Test/Lit"])


if __name__ == "__main__":
    unittest.main()
