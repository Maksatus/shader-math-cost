"""project/materials.py and project/shaders.py on a synthetic Unity project in a temporary folder.

Run: python -m unittest paretogpu.project.test_project   (from the repository root)
"""
import json
import os
import shutil
import tempfile
import unittest

from paretogpu.project import materials, shaders

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
        self.assertEqual(sorted(keys), [("Test/Lit", 0, 0, "Forward Opaque", ("_CLUSTERED", "_EMISSION"))])
        self.assertEqual(sorted(x["material"] for x in skipped),
                         ["Assets/M/builtin.mat", "Assets/M/glass.mat", "Assets/M/water.mat"])
        # with the editor's pass lists: every color pass the material does not switch off
        lit = [{"subshader": 0, "pass_index": 0, "pass": "Forward Opaque", "light_mode": "Forward Opaque"},
               {"subshader": 0, "pass_index": 1, "pass": "Forward Transparent", "light_mode": "Forward Transparent"},
               {"subshader": 0, "pass_index": 2, "pass": "ShadowCaster", "light_mode": "ShadowCaster"}]
        keys, skipped = shaders.plan(ms, info, {"Test/Lit": lit, "Custom/Water": lit[:1]})
        self.assertEqual(sorted(keys), [
            ("Custom/Water", 0, 0, "Forward Opaque", ("_CLUSTERED",)),
            ("Test/Lit", 0, 0, "Forward Opaque", ("_CLUSTERED", "_EMISSION")),
            ("Test/Lit", 0, 1, "Forward Transparent", ("_ALPHA", "_FOG"))])
        self.assertEqual(keys[("Test/Lit", 0, 1, "Forward Transparent", ("_ALPHA", "_FOG"))], ["Assets/M/glass.mat"])
        self.assertEqual([x["material"] for x in skipped], ["Assets/M/builtin.mat"])


if __name__ == "__main__":
    unittest.main()
