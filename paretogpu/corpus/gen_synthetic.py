"""Synthetic corpus with known answers (plan item A0.1).

Writes synthetic/*.frag|*.vert and expected.json. Expected cycles of the
function chains come from the shader-math-cost table (docs/mali_math_cost.csv):
    expected[pipe] = baseline chain[pipe] + N * per-call cost[pipe]
where the baseline is the same chain without the function (malioc, longest path).
Chains are long enough to be bound by arithmetic or texture, not by varyings.
Hand-written shaders (branch, spilling, vertex) get flag and relation checks.

Usage: python gen_synthetic.py   (then check.py)
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
from paretogpu.adapters import malioc as mali
from paretogpu.core.shadergen import build, header

CORE, API = "Mali-G78", "gles"
TOL = 0.10
OUT = os.path.join(HERE, "synthetic")
TABLE = os.path.join(ROOT, "docs", "mali_math_cost.csv")
PIPES = ("fma", "cvt", "sfu", "t")  # what the table covers; V and LS are fixed overhead

# name, function id in the table, table variant, GLSL type, precision, expression, N, combine
CHAINS = [
    ("sin_x4",      "sin",   "float",  "float", "highp",   "sin({x})",            4,  True),
    ("sin_x16",     "sin",   "float",  "float", "highp",   "sin({x})",            16, True),
    ("sin4_x16",    "sin",   "float4", "vec4",  "highp",   "sin({x})",            16, True),
    ("acos_x8",     "acos",  "float",  "float", "highp",   "acos({x})",           8,  True),
    ("div_x48",     "div",   "float",  "float", "highp",   "{a} / {x}",           48, True),
    ("sqrt_x16",    "sqrt",  "float",  "float", "highp",   "sqrt({x})",           16, True),
    ("tex_x24",     "tex2D", "float",  "vec4",  "highp",   "texture(uT2, {x}.xy)", 24, True),
    ("mad4_x64",    "mad",   "float4", "vec4",  "highp",   "{x} * {a} + {b}",     64, False),
    ("mad4h_x64",   "mad",   "half4",  "vec4",  "mediump", "{x} * {a} + {b}",     64, False),
]


def branch_src():
    """sin_x16 inside a branch: longest path = the chain, shortest path = almost nothing."""
    s = build(API, "highp", "float", "sin({x})", 16, True)
    head, body = s.split("void main() {\n")
    lines = body.splitlines()
    init, steps, tail = lines[0], lines[1:-2], lines[-2:]
    return (head + "void main() {\n" + init + "\n  if (vX.y > 0.5) {\n"
            + "\n".join("  " + l for l in steps) + "\n  }\n" + "\n".join(tail) + "\n")


def spill_src(n=32):
    """n vec4 values live at once: needs more than 64 registers, spills to the stack."""
    s = header(API, "highp")
    s += "void main() {\n"
    for i in range(n):
        s += f"  vec4 t{i} = sin(vP{i % 8} * {i + 1}.0 + vX.{'xyzw'[i % 4]});\n"
    s += "  vec4 r = vX;\n"
    for i in range(n):
        s += f"  r = r * t{n - 1 - i} + t{(i * 7) % n};\n"
    return s + "  o = r;\n}\n"


def loop_src():
    """sin in a loop with a uniform trip count (like the URP additional lights loop):
    malioc cannot bound the longest path and reports N/A."""
    return ("#version 310 es\nprecision highp float;\n"
            "layout(std140, binding=0) uniform U { int uCount; };\n"
            "layout(location=0) in highp vec4 vX;\n"
            "layout(location=0) out highp vec4 o;\n"
            "void main() {\n  vec4 x = vX;\n"
            "  for (int i = 0; i < uCount; i++) { x = sin(x) + vX; }\n"
            "  o = x;\n}\n")


def clean_src():
    """One texture sample times a uniform color: nothing to optimise."""
    return ("#version 310 es\nprecision mediump float;\n"
            "layout(std140, binding=0) uniform U { mediump vec4 uColor; };\n"
            "layout(binding=1) uniform mediump sampler2D uTex;\n"
            "layout(location=0) in highp vec2 vUV;\n"
            "layout(location=0) out mediump vec4 o;\n"
            "void main() { o = texture(uTex, vUV) * uColor; }\n")


VERT_HEAD = ("#version 310 es\nprecision highp float;\n"
             "layout(std140, binding=0) uniform U { mat4 uMVP; vec4 uParams; };\n"
             "layout(location=0) in vec4 aPos;\nlayout(location=1) in vec2 aUV;\n"
             "layout(location=0) out vec2 vUV;\nlayout(location=1) out vec4 vExtra;\n")


def vert_basic():
    return VERT_HEAD + ("void main() {\n  gl_Position = uMVP * aPos;\n"
                        "  vUV = aUV;\n  vExtra = aPos;\n}\n")


def vert_wave(n=8):
    """sin chain on the position: Position variant gets heavy."""
    s = VERT_HEAD + "void main() {\n  vec4 p = aPos;\n"
    for i in range(n):
        s += f"  p.y += sin(p.x * {i + 1}.0 + p.z + uParams.x) * uParams.y;\n"
    return s + "  gl_Position = uMVP * p;\n  vUV = aUV;\n  vExtra = aPos;\n}\n"


def vert_varying(n=8):
    """sin chain on a varying only: Varying variant gets heavy, Position stays basic."""
    s = VERT_HEAD + "void main() {\n  gl_Position = uMVP * aPos;\n  vec4 e = aPos;\n"
    for i in range(n):
        s += f"  e = sin(e * {i + 1}.0 + aUV.xyxy + uParams);\n"
    return s + "  vUV = aUV;\n  vExtra = e;\n}\n"


def table_costs():
    rows = {}
    with open(TABLE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["gpu"] == CORE and r["api"] == ("GLES" if API == "gles" else "Vulkan"):
                rows[(r["func"], r["variant"])] = r
    return rows


def per_call(r):
    c = {p: float(r[p] or 0) for p in ("fma", "cvt", "sfu")}
    c["t"] = float(r["cycles"]) if r["bound"] == "TEX" else 0.0
    return c


def longest(src, stage="fragment"):
    m = mali.measure(src, CORE, API, stage)
    if not m["ok"]:
        raise RuntimeError(m["error"])
    return m["variants"]["main"]["longest"]


def bound_cycles(c):
    return max(c[p] for p in PIPES)


def main():
    os.makedirs(OUT, exist_ok=True)
    table = table_costs()
    shaders, files = {}, {}

    for name, fid, variant, t, prec, expr, n, combine in CHAINS:
        src = build(API, prec, t, expr, n, combine)
        base = longest(build(API, prec, t, "{x}", n, True) if combine else build(API, prec, t, expr, 0, False))
        r = table[(fid, variant)]
        cost = per_call(r)
        exp = {p: round(base[p] + n * cost[p], 5) for p in PIPES}
        files[name + ".frag"] = src
        shaders[name + ".frag"] = {
            "what": f"{n} x {fid} ({variant}), chain from shadergen.py",
            "checks": [{"variant": "main", "path": "longest", "cycles": exp, "tol": TOL,
                        "from": f"baseline chain + {n} x table[{fid}, {variant}] "
                                f"(fma {cost['fma']}, cvt {cost['cvt']}, sfu {cost['sfu']}, t {cost['t']})"},
                       {"variant": "main", "spilling": False}],
            "expected_bound_cycles": round(bound_cycles(exp), 5),
        }

    # branch: longest path as sin_x16, shortest path close to the empty chain
    sin16 = shaders["sin_x16.frag"]["checks"][0]["cycles"]
    empty = longest(build(API, "highp", "float", "{x}", 0, True))
    files["branch_sin16.frag"] = branch_src()
    shaders["branch_sin16.frag"] = {
        "what": "sin_x16 inside if (vX.y > 0.5)",
        "checks": [{"variant": "main", "path": "longest", "cycles": sin16, "tol": TOL,
                    "from": "same as sin_x16"},
                   {"variant": "main", "path": "shortest", "max_bound_cycles": bound_cycles(empty) + 0.5,
                    "from": "empty shader + 0.5 cycle for the branch"}],
        "expected_bound_cycles": shaders["sin_x16.frag"]["expected_bound_cycles"],
    }

    files["spill_32xvec4.frag"] = spill_src()
    shaders["spill_32xvec4.frag"] = {
        "what": "32 vec4 sin values alive at once",
        "checks": [{"variant": "main", "spilling": True},
                   {"variant": "main", "bound": ["ls"]}],
    }

    files["loop_dynamic.frag"] = loop_src()
    shaders["loop_dynamic.frag"] = {
        "what": "sin in a loop with a uniform trip count",
        "checks": [{"variant": "main", "path": "longest", "na": True},
                   {"variant": "main", "path": "total", "na": False}],
    }

    files["clean.frag"] = clean_src()
    shaders["clean.frag"] = {
        "what": "texture * uniform color, mediump",
        "checks": [{"variant": "main", "path": "longest", "max_bound_cycles": 0.5},
                   {"variant": "main", "spilling": False}],
        "expected_bound_cycles": 0.25,
    }

    files["basic.vert"] = vert_basic()
    files["wave_pos.vert"] = vert_wave()
    files["sin_varying.vert"] = vert_varying()
    shaders["basic.vert"] = {"what": "MVP transform, pass-through varyings", "checks": []}
    shaders["wave_pos.vert"] = {
        "what": "8 sin on the position",
        "checks": [{"greater": ["wave_pos.vert", "position", "sfu"], "than": ["basic.vert", "position", "sfu"],
                    "by": 8 * per_call(table[("sin", "float")])["sfu"] * 0.9}],
    }
    shaders["sin_varying.vert"] = {
        "what": "8 vec4 sin on a varying, position as basic.vert",
        "checks": [{"greater": ["sin_varying.vert", "varying", "sfu"], "than": ["sin_varying.vert", "position", "sfu"],
                    "by": 8 * per_call(table[("sin", "float4")])["sfu"] * 0.9},
                   {"equal": ["sin_varying.vert", "position", "bound_cycles"],
                    "to": ["basic.vert", "position", "bound_cycles"], "tol": TOL}],
    }

    # heaviest first, by the expected bottleneck on the longest path
    frag = [k for k, v in shaders.items() if "expected_bound_cycles" in v]
    order = sorted(frag, key=lambda k: -shaders[k]["expected_bound_cycles"])

    for fn, src in files.items():
        with open(os.path.join(OUT, fn), "w", newline="\n") as f:
            f.write(src)
    exp = {"core": CORE, "api": API, "malioc": mali.version(), "table": "docs/mali_math_cost.csv",
           "order_fragment": order, "shaders": shaders}
    with open(os.path.join(HERE, "expected.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(exp, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"{len(files)} shaders -> {OUT}")
    print("order:", ", ".join(order))


if __name__ == "__main__":
    main()
