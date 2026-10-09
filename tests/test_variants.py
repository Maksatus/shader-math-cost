"""app/variants.run() (app/rules.CompileRule): which compiled variants are reused and which are compiled again
(fingerprints, deleted files, failures), with Unity and malioc replaced by stubs; synthetic names, temporary folder.

Run: python -m unittest discover tests   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from paretogpu.adapters.gpu import backend
from paretogpu.app import rules, variants
from paretogpu.model.variant import NOT_FINISHED, VariantKey
from paretogpu.store import variant_store
from paretogpu.store.memo import Memo
from paretogpu.views.reporter import QUIET

EV = {"index": 0, "kind": "draw", "shader": "Test/Lit", "subshader": 0, "pass_index": 0, "pass": "Forward",
      "keywords": ["_A"]}
KEY = VariantKey.of(EV)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.compiled = []
        self.fp = {"Test/Lit": "fp1"}
        self.result = {}  # errors of the next compile

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def compile_stub(self, project, keys, root, platforms, timeout=1800, rep=None):
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
            result["variants"].append({"id": i, "files": files, "errors": errs, "fingerprint": self.fp.get(k.shader)})
        return variant_store.place(root, raw, keys, result)

    def run_once(self, editor=True, **kw):
        with mock.patch.object(rules.unity, "editor_ready", return_value=editor), \
                mock.patch.object(rules, "current_fingerprints", side_effect=lambda p, s, r: dict(self.fp)), \
                mock.patch.object(rules, "compile_variants", side_effect=self.compile_stub), \
                mock.patch.object(backend(), "measure", return_value={"ok": False, "error": "stub"}):
            return variants.run([EV], "project", self.root, ["Mali-G78"], ["vulkan"], rep=QUIET, **kw)

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
        self.result = {KEY: [NOT_FINISHED]}
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



class PassesTest(unittest.TestCase):
    """shader_passes(): the passes of a shader are asked again only when its fingerprint changes."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.memo = Memo(os.path.join(self.dir, "memo.sqlite"))
        self.fps = {"A": "fa", "B": "builtin", "C": "builtin", "Gone": None}
        self.asked = []

    def tearDown(self):
        self.memo.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def query(self, project, entry, shaders, root):
        if entry == "Fingerprints":
            return {n: self.fps[n] for n in shaders}
        self.asked.append(sorted(shaders))
        return {n: None if n == "Gone" else [{"pass": f"{n}-{self.fps[n]}"}] for n in shaders}

    def passes(self):
        with mock.patch.object(variants, "shader_query", self.query):
            return variants.shader_passes("P", ["A", "B", "C", "Gone"], self.dir, self.memo)

    def test_remembered_by_fingerprint(self):
        first = self.passes()
        self.assertEqual(first["A"], [{"pass": "A-fa"}])
        self.assertEqual(first["C"], [{"pass": "C-builtin"}])
        self.assertIsNone(first["Gone"])
        self.assertEqual(self.passes(), first)
        self.fps["A"] = "fa2"
        self.assertEqual(self.passes()["A"], [{"pass": "A-fa2"}])
        self.assertEqual(self.asked, [["A", "B", "C", "Gone"], ["Gone"], ["A", "Gone"]])


if __name__ == "__main__":
    unittest.main()
