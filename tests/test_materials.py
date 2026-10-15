"""adapters/unity/assets.py and core/materials.py on a synthetic Unity project."""
import json
import os
import shutil
import tempfile
import unittest
from collections import Counter

from paretogpu.adapters.unity import assets as materials
from paretogpu.core import materials as shaders
from paretogpu.model.variant import VariantKey

MAT = """%YAML 1.1
--- !u!21 &2100000
Material:
  m_Name: {name}
  m_Shader: {{fileID: 4800000, guid: {guid}, type: 3}}
  m_ValidKeywords:
{keywords}  m_InvalidKeywords: []
  disabledShaderPasses:
{disabled}  m_SavedProperties:
    serializedVersion: 3
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def mat(name, guid, keywords=(), disabled=()):
    return MAT.format(name=name, guid=guid, keywords="".join(f"  - {k}\n" for k in keywords),
                      disabled="".join(f"  - {d}\n" for d in disabled))


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.p = tempfile.mkdtemp()
        a = os.path.join(self.p, "Assets")
        write(os.path.join(a, "Shaders", "Lit.shader"), 'Shader "Test/Lit" {\n SubShader { Pass { } }\n}\n')
        write(os.path.join(a, "Shaders", "Lit.shader.meta"), "fileFormatVersion: 2\nguid: " + "1" * 32 + "\n")
        graph = [{"m_Type": "UnityEditor.ShaderGraph.GraphData", "m_Path": "Custom"}, {"m_Type": "Node"}]
        write(os.path.join(a, "Shaders", "Water.shadergraph"), "\n\n".join(json.dumps(g, indent=4) for g in graph))
        write(os.path.join(a, "Shaders", "Water.shadergraph.meta"), "fileFormatVersion: 2\nguid: " + "2" * 32 + "\n")
        write(os.path.join(a, "M", "opaque.mat"), mat("opaque", "1" * 32, ["_EMISSION"], ["Forward Transparent"]))
        write(os.path.join(a, "M", "glass.mat"), mat("glass", "1" * 32, ["_ALPHA"], ["Forward Opaque"]))
        write(os.path.join(a, "M", "water.mat"), mat("water", "2" * 32))
        write(os.path.join(a, "M", "builtin.mat"), mat("b", "0000000000000000f000000000000000").replace("4800000", "46"))

    def tearDown(self):
        shutil.rmtree(self.p, ignore_errors=True)

    def test_scan(self):
        ms = {m["path"]: m for m in materials.scan(self.p)}
        self.assertEqual(ms["Assets/M/opaque.mat"]["shader"], "Test/Lit")
        self.assertEqual(ms["Assets/M/opaque.mat"]["keywords"], ["_EMISSION"])
        self.assertEqual(ms["Assets/M/opaque.mat"]["disabled_passes"], ["Forward Transparent"])
        self.assertEqual(ms["Assets/M/water.mat"]["shader"], "Custom/Water")
        self.assertIn("built-in", ms["Assets/M/builtin.mat"]["error"])

    def test_plan(self):
        ms = materials.scan(self.p)
        snap = [[{"kind": "draw", "stage": "opaque", "shader": "Test/Lit", "subshader": 0, "pass_index": 0,
                  "pass": "Forward Opaque", "light_mode": "Forward Opaque", "global_keywords": ["_CLUSTERED"]},
                 {"kind": "draw", "stage": "transparent", "shader": "Other", "subshader": 0, "pass_index": 1,
                  "pass": "Forward Transparent", "light_mode": "Forward Transparent", "global_keywords": ["_FOG"]}]]
        info = shaders.snapshot_info(snap)
        # without the editor: only the passes the snapshots drew, the water shader is skipped
        keys, skipped = shaders.plan(ms, info)
        self.assertEqual(sorted(keys), [VariantKey("Test/Lit", 0, 0, "Forward Opaque", ("_CLUSTERED", "_EMISSION"))])
        self.assertEqual(sorted(x["material"] for x in skipped),
                         ["Assets/M/builtin.mat", "Assets/M/glass.mat", "Assets/M/water.mat"])
        # with the editor's pass lists: every color pass the material does not switch off
        lit = [{"subshader": 0, "pass_index": 0, "pass": "Forward Opaque", "light_mode": "Forward Opaque"},
               {"subshader": 0, "pass_index": 1, "pass": "Forward Transparent", "light_mode": "Forward Transparent"},
               {"subshader": 0, "pass_index": 2, "pass": "ShadowCaster", "light_mode": "ShadowCaster"}]
        keys, skipped = shaders.plan(ms, info, {"Test/Lit": lit, "Custom/Water": lit[:1]})
        self.assertEqual(sorted(keys), [
            VariantKey("Custom/Water", 0, 0, "Forward Opaque", ("_CLUSTERED",)),
            VariantKey("Test/Lit", 0, 0, "Forward Opaque", ("_CLUSTERED", "_EMISSION")),
            VariantKey("Test/Lit", 0, 1, "Forward Transparent", ("_ALPHA", "_FOG"))])
        self.assertEqual(keys[VariantKey("Test/Lit", 0, 1, "Forward Transparent", ("_ALPHA", "_FOG"))],
                         ["Assets/M/glass.mat"])
        self.assertEqual([x["material"] for x in skipped], ["Assets/M/builtin.mat"])

    def test_pass_keywords_and_disabled_light_modes(self):
        write(os.path.join(self.p, "Assets", "M", "opaque.mat"),
              mat("opaque", "1" * 32, ["_EMISSION", "_AO"], ["forward transparent", "SRPDEFAULTUNLIT"]))
        ms = [m for m in materials.scan(self.p) if m["path"] == "Assets/M/opaque.mat"]
        info = shaders.snapshot_info([[]])
        lit = [{"subshader": 0, "pass_index": 0, "pass": "Forward Opaque", "light_mode": "Forward Opaque",
                "keywords": ["_EMISSION"]},
               {"subshader": 0, "pass_index": 1, "pass": "Forward Transparent", "light_mode": "Forward Transparent",
                "keywords": ["_EMISSION", "_AO"]},
               {"subshader": 0, "pass_index": 2, "pass": "Unlit", "light_mode": None, "keywords": None}]
        info["by_light_mode"] = {"Forward Opaque": Counter({(): 1}), "Forward Transparent": Counter({(): 1}),
                                 None: Counter({(): 1})}
        keys, _ = shaders.plan(ms, info, {"Test/Lit": lit})
        self.assertEqual(sorted(keys), [VariantKey("Test/Lit", 0, 0, "Forward Opaque", ("_EMISSION",))])


if __name__ == "__main__":
    unittest.main()


class BuildPipelineTest(unittest.TestCase):
    """The asset an Android build starts with: its default quality level's, else Graphics settings'."""

    def setUp(self):
        self.p = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.p, "ProjectSettings"))
        os.makedirs(os.path.join(self.p, "Assets", "Settings"))
        for name, guid in (("Mobile_RP", "a" * 32), ("PC_RP", "b" * 32)):
            with open(os.path.join(self.p, "Assets", "Settings", f"{name}.asset.meta"), "w") as f:
                f.write(f"fileFormatVersion: 2\nguid: {guid}\n")

    def tearDown(self):
        shutil.rmtree(self.p, ignore_errors=True)

    def write(self, android, mobile_rp):
        ref = "{fileID: 11400000, guid: %s, type: 2}"
        with open(os.path.join(self.p, "ProjectSettings", "QualitySettings.asset"), "w") as f:
            f.write("QualitySettings:\n  m_QualitySettings:\n  - serializedVersion: 5\n    name: PC\n"
                    f"    customRenderPipeline: {ref % ('b' * 32)}\n  - serializedVersion: 5\n    name: Mobile\n"
                    f"    customRenderPipeline: {ref % ('a' * 32) if mobile_rp else '{fileID: 0}'}\n"
                    f"  m_PerPlatformDefaultQuality:\n    Android: {android}\n    Standalone: 0\n")
        with open(os.path.join(self.p, "ProjectSettings", "GraphicsSettings.asset"), "w") as f:
            f.write(f"GraphicsSettings:\n  m_CustomRenderPipeline: {ref % ('b' * 32)}\n")

    def test_android_quality_level_then_graphics_settings(self):
        self.write(1, True)
        self.assertEqual(materials.build_pipeline(self.p), {"name": "Mobile_RP", "hash": None, "quality": "Mobile",
                                                            "path": "Assets/Settings/Mobile_RP.asset"})
        self.write(1, False)
        self.assertEqual(materials.build_pipeline(self.p)["path"], "Assets/Settings/PC_RP.asset")
