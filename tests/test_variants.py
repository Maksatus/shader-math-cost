"""app/variants.run() (rules.CompileRule): which compiled variants are reused or recompiled; Unity, malioc stubbed."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from paretogpu.adapters.gpu import backend
from paretogpu.app import rules, variants
from paretogpu.model.variant import CODE, NOT_FINISHED, STAGE, STOPPED, STOPPED_TWICE, VariantKey
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

    def test_failed_recompile_drops_the_old_files(self):
        self.run_once()
        self.fp["Test/Lit"] = "fp2"
        self.result = {KEY: ["vulkan frag: error"]}
        st = self.run_once()
        self.assertEqual(st["files"][KEY], {})
        self.assertEqual(st["errors"][KEY], ["vulkan frag: error"])
        st = self.run_once()
        self.assertEqual(len(self.compiled), 2)
        self.assertEqual((st["files"][KEY], st["errors"][KEY]), ({}, ["vulkan frag: error"]))
        self.fp["Test/Lit"] = "fp3"
        self.result = {}
        st = self.run_once()
        self.assertEqual(len(self.compiled), 3)
        self.assertEqual(set(st["files"][KEY]), {("vulkan", "vert"), ("vulkan", "frag")})

    def test_errors_of_another_platform(self):
        self.run_once()
        self.result = {KEY: ["gles3 frag: error"]}
        with mock.patch.object(rules.unity, "editor_ready", return_value=True), \
                mock.patch.object(rules, "current_fingerprints", side_effect=lambda p, s, r: dict(self.fp)), \
                mock.patch.object(rules, "compile_variants", side_effect=self.compile_stub):
            variants.compile_keys([KEY], "project", self.root, ["gles3"], QUIET)
        self.result = {}
        self.fp["Test/Lit"] = "fp2"
        st = self.run_once()
        self.assertEqual(len(self.compiled), 3)
        self.assertEqual(st["errors"][KEY], [])
        with open(os.path.join(self.root, "variants.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["variants"][0]["errors"], ["gles3 frag: error"])

    def test_failure_survives_a_run_without_the_editor(self):
        self.result = {KEY: ["vulkan frag: error"]}
        self.run_once()
        self.run_once(editor=False, compile_missing=False)
        self.run_once()
        self.assertEqual(len(self.compiled), 1)

    def test_a_variant_that_stops_unity_twice_is_not_compiled_again(self):
        self.result = {KEY: [NOT_FINISHED, STOPPED]}
        self.run_once()
        self.run_once()
        st = self.run_once()
        self.assertEqual(len(self.compiled), 2)
        self.assertEqual(st["errors"][KEY], [STOPPED_TWICE])

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


class CapturedTest(unittest.TestCase):
    """Device frame code (variants.identify / place_captured): matched to the project's SPIR-V, nothing compiled."""

    def setUp(self):
        self.root, self.frame = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(self.frame, "device", "shaders"))
        self.key = VariantKey("Test/Lit", 0, 1, "Forward", ("_A",))
        self.twin = VariantKey("Test/Lit", 0, 1, "Forward", ("_A", "_NOTHING"))  # the same code
        self.shadow = VariantKey("Test/Lit", 0, 2, "ShadowCaster", ())
        other = VariantKey("Test/Other", 0, 2, "ShadowCaster", ())
        for keys in ((self.twin, self.key, self.shadow), (other,)):
            folder = os.path.join(self.root, keys[0].folder("vulkan"))
            os.makedirs(folder)
            manifest = []
            for k in keys:
                code = (("vert", b"SV"), ("frag", b"SF")) if k.pass_name == "ShadowCaster" else                     (("vert", b"VS"), ("frag", b"FS"))
                for s, data in code:
                    fn = k.base_name + f".{s}.spv"
                    with open(os.path.join(folder, fn), "wb") as f:
                        f.write(data)
                    manifest.append({"shader": k.shader, "subshader": k.subshader, "pass_index": k.pass_index,
                                     "pass": k.pass_name, "keywords": list(k.keywords), "stage": STAGE[s],
                                     "platform": "vulkan", "file": fn})
            with open(os.path.join(folder, "manifest.json"), "w") as f:
                json.dump(manifest, f)
        for name, data in (("v.vert.spv", b"VS"), ("f.frag.spv", b"FS"), ("g.frag.spv", b"OTHER"),
                           ("s.vert.spv", b"SV")):
            with open(os.path.join(self.frame, "device", "shaders", name), "wb") as f:
                f.write(data)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.frame, ignore_errors=True)

    def ev(self, frag, shader="Test/Lit", mesh="Rock"):
        code = {"vert": "device/shaders/v.vert.spv", "frag": f"device/shaders/{frag}", "hash": frag[:1]} if frag             else {"vert": "device/shaders/s.vert.spv", "hash": "s"}
        return {"index": 0, "kind": "draw", "shader": shader, "pass": None, "subshader": 0, "pass_index": 0,
                "keywords": [], "meshes": [mesh], "code": code}

    def test_identify_and_place(self):
        evs = [self.ev("f.frag.spv"), self.ev("g.frag.spv"), self.ev("f.frag.spv", "Other")]
        self.assertEqual(variants.identify(evs, self.frame, self.root, QUIET), 1)
        self.assertEqual(VariantKey.of(evs[0]), self.key)  # of the twins, the fewest keywords
        self.assertEqual((evs[0]["pass"], evs[0]["keywords"], evs[0]["code"]["variants"]), ("Forward", ["_A"], 2))
        self.assertEqual(VariantKey.of(evs[1]).keywords, (CODE + "g",))  # code the folder has never seen
        self.assertEqual(VariantKey.of(evs[2]).shader, "Other")  # another shader's code is not its variant
        placed = variants.place_captured(evs, self.frame, self.root)
        self.assertEqual(placed[self.key], {("vulkan", "vert"): f"Test_Lit_vulkan/{self.key.base_name}.vert.spv",
                                            ("vulkan", "frag"): f"Test_Lit_vulkan/{self.key.base_name}.frag.spv"})
        self.assertEqual(placed[VariantKey.of(evs[1])][("vulkan", "frag")], "Test_Lit_device/g.frag.spv")
        with open(os.path.join(self.root, "Test_Lit_device", "g.frag.spv"), "rb") as f:
            self.assertEqual(f.read(), b"OTHER")
        with mock.patch.object(rules, "compile_variants") as compile_, \
                mock.patch.object(backend(), "measure", return_value={"ok": False, "error": "stub"}):
            st = variants.run(evs, None, self.root, ["Mali-G78"], ["vulkan"], rep=QUIET, captured=placed)
        compile_.assert_not_called()  # the code is the variant: nothing to compile, no editor
        self.assertTrue(st["checked"])
        self.assertEqual(st["captured"], set(placed))
        self.assertEqual(st["files"][self.key], placed[self.key])

    def test_depth_only_by_its_mesh(self):
        color = self.ev("f.frag.spv")
        rock, tree = self.ev(None, "Test/Other (shared shader)"), self.ev(None, "Test/Other (shared shader)", "Tree")
        self.assertEqual(variants.identify([color, rock, tree], self.frame, self.root, QUIET), 2)
        self.assertEqual(VariantKey.of(rock), self.shadow)
        self.assertEqual(rock["code"]["files"], {"vert": f"Test_Lit_vulkan/{self.shadow.base_name}.vert.spv"})
        self.assertNotIn("variant", tree["code"])


class LoopsPerCoreTest(unittest.TestCase):
    """Loops are forced only on cores whose longest path is N/A; one core's N/A must not change another's price."""

    @staticmethod
    def record(longest):
        c = {"arith": 3.0, "fma": 3.0}
        v = {"longest": c if longest else None, "shortest": c, "total": c, "work_regs": 16, "spilling": False,
             "fp16_pct": 50}
        return {"ok": True, "variants": {"main": v}}

    def test_only_the_cores_without_a_longest_path(self):
        key = VariantKey("S", 0, 0, "P")
        state = {"keys": [key], "files": {key: {("vulkan", "frag"): "S.frag.spv"}},
                 "measurements": {"S.frag.spv": {"Mali-G78": self.record(True), "Mali-G715": self.record(False)}}}
        asked = []

        class Engine:
            def __init__(self, *a, **k):
                pass

            def get(self, rule, keys):
                asked.extend(keys)
                return {k: {"loops": [{}]} for k in keys}
        with mock.patch.object(variants, "Engine", Engine):
            out = variants.loops_of(state, "root", ["Mali-G78", "Mali-G715"], rep=QUIET)
        self.assertEqual(asked, [("S.frag.spv", "Mali-G715")])
        self.assertEqual(list(out["S.frag.spv"]), ["Mali-G715"])


if __name__ == "__main__":
    unittest.main()
