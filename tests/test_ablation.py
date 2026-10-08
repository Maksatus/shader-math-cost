"""Ablation (core/ablation.py, app/ablation.py, plan A3.2) on synthetic shaders: no project data.

Run: python -m unittest discover tests   (from the repository root)
"""
import os
import unittest

from paretogpu.adapters import malioc as mali
from paretogpu.app import ablation as ablation_run
from paretogpu.core import ablation

HERE = os.path.dirname(os.path.abspath(__file__))
SYNTH = os.path.join(HERE, "..", "paretogpu", "corpus", "synthetic")
CORE = "Mali-G78"

# HLSLcc-like: globals declared before main, one assignment per line, a loop and an if / else
UNITY_LIKE = """#version 310 es
precision highp float;
layout(location = 0) in highp vec4 vs_TEXCOORD0;
layout(location = 0) out mediump vec4 SV_Target0;
vec4 u_xlat0;
mediump vec3 u_xlat16_1;
float u_xlat2;
bool u_xlatb3;
int u_xlati4;
void main()
{
    u_xlat0 = vs_TEXCOORD0 * vs_TEXCOORD0;
    u_xlat2 = sin(u_xlat0.x);
    u_xlat16_1.xyz = vec3(u_xlat2) * u_xlat0.yzw;
    u_xlati4 = 0;
    while(true){
        u_xlatb3 = u_xlati4 >= int(vs_TEXCOORD0.w);
        if(u_xlatb3){break;}
        u_xlat16_1.xyz = u_xlat16_1.xyz * 0.5;
        u_xlati4 = u_xlati4 + 1;
    }
    if(u_xlatb3){
        u_xlat2 = 1.0;
    } else {
        u_xlat2 = 2.0;
    }
    SV_Target0.xyz = u_xlat16_1.xyz * u_xlat2;
    SV_Target0.w = 1.0;
    return;
}
"""


def read(name):
    with open(os.path.join(SYNTH, name), encoding="utf-8") as f:
        return f.read()


class Parse(unittest.TestCase):
    def test_statements_and_types(self):
        p = ablation.parse(UNITY_LIKE)
        a = [s for s in p["statements"] if s["type"] == "assign"]
        self.assertEqual([s["var"] for s in a][:3], ["u_xlat0", "u_xlat2", "u_xlat16_1"])
        s = a[2]
        self.assertEqual((s["kind"], s["width"], s["prec"], s["comps"]), ("f", 3, "mediump", [0, 1, 2]))
        self.assertTrue(next(x for x in a if x["text"].startswith("u_xlat16_1.xyz = u_xlat16_1"))["loop"])

    def test_parents(self):
        p = ablation.parse(UNITY_LIKE)
        parent, _ = ablation.tree(p)
        by = {p["statements"][i]["text"]: par for i, par in parent.items()}
        text = lambda i: p["statements"][i]["text"] if i is not None else None
        # sin(x) feeds only the multiplication: its child
        self.assertEqual(text(by["u_xlat2 = sin(u_xlat0.x);"]), "u_xlat16_1.xyz = vec3(u_xlat2) * u_xlat0.yzw;")
        # u_xlat0 is read by sin and by the multiplication, but both die with the multiplication: it dies too
        self.assertEqual(text(by["u_xlat0 = vs_TEXCOORD0 * vs_TEXCOORD0;"]),
                         "u_xlat16_1.xyz = vec3(u_xlat2) * u_xlat0.yzw;")
        # the value before the loop is read by the loop and by the output after it (the loop may not run):
        # removing the loop's statement keeps it, removing the output kills both
        self.assertEqual(text(by["u_xlat16_1.xyz = vec3(u_xlat2) * u_xlat0.yzw;"]),
                         "SV_Target0.xyz = u_xlat16_1.xyz * u_xlat2;")
        # both branches of the if feed the output
        self.assertEqual(text(by["u_xlat2 = 1.0;"]), "SV_Target0.xyz = u_xlat16_1.xyz * u_xlat2;")
        self.assertEqual(text(by["u_xlat2 = 2.0;"]), "SV_Target0.xyz = u_xlat16_1.xyz * u_xlat2;")
        # outputs are read by the end of the shader
        self.assertIsNone(by["SV_Target0.w = 1.0;"])

    def test_dead_code_takes_loops_and_conditions(self):
        """Without the output the compiler drops the loop too: its counter, its exit test and the if after it."""
        p = ablation.parse(UNITY_LIKE)
        _, users = ablation.tree(p)
        dead = ablation.dead_sets(p, users)
        sid = {s["text"]: s["id"] for s in p["statements"]}
        gone = {p["statements"][i]["text"] for i in dead[sid["SV_Target0.xyz = u_xlat16_1.xyz * u_xlat2;"]]}
        for t in ("while(true){", "u_xlatb3 = u_xlati4 >= int(vs_TEXCOORD0.w);", "if(u_xlatb3){break;}",
                  "u_xlati4 = u_xlati4 + 1;", "u_xlati4 = 0;", "if(u_xlatb3){", "u_xlat2 = sin(u_xlat0.x);"):
            self.assertIn(t, gone)
        # the other output keeps nothing alive but itself
        self.assertEqual(dead[sid["SV_Target0.w = 1.0;"]], {sid["SV_Target0.w = 1.0;"]})

    def test_ablated_source(self):
        p = ablation.parse(UNITY_LIKE)
        sid = next(s["id"] for s in p["statements"] if s["text"].startswith("u_xlat16_1.xyz = vec3"))
        src = ablation.ablated(p, sid, "fragment")
        self.assertIn(f"layout(location = 1) in mediump vec4 {ablation.NAME};", src)
        self.assertIn(f"u_xlat16_1.xyz = {ablation.NAME}.xyz;", src)
        bid = next(s["id"] for s in p["statements"] if s["text"].startswith("u_xlatb3 ="))
        self.assertIn(f"flat in highp ivec4 {ablation.NAME};", ablation.ablated(p, bid, "fragment"))

    def test_synthetic_chain(self):
        p = ablation.parse(read("sin_x4.frag"))
        parent, _ = ablation.tree(p)
        assigns = [s["id"] for s in p["statements"] if s["type"] == "assign"]
        # x = sin(x) + v: every statement feeds only the next one, the last feeds the output
        self.assertEqual([parent[i] for i in assigns], assigns[1:] + [None])


@unittest.skipUnless(os.path.exists(mali.MALIOC or ""), "malioc is not installed")
class Measure(unittest.TestCase):
    def test_sin_is_about_8_fma_on_valhall(self):
        """Ready-when of A3.2: the self cost of a sin ≈ 8 FMA on Valhall."""
        sin = ablation_run.run(read("sin_x4.frag"), "fragment", [CORE], jobs=4, progress=lambda *a: None)
        mad = ablation_run.run(read("mad4_x64.frag"), "fragment", [CORE], jobs=4, progress=lambda *a: None)
        rows = lambda r: r["by_core"][CORE]["stmts"]
        sins = [rows(sin)[i]["self"]["fma"] for i, s in sin["statements"].items() if "sin(" in s["text"]]
        fma = mad["by_core"][CORE]["base"]["pipes"]["fma"] / (64 * 4)  # 64 vec4 mads
        self.assertAlmostEqual(sorted(sins)[len(sins) // 2] / fma, 8.0, delta=1.0)


if __name__ == "__main__":
    unittest.main()
