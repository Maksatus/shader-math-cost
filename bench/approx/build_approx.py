"""Builds docs/approx.js: cheaper replacements (approximations) of table functions.

For every approximation: HLSL code, valid input range, max error in float32 and float16
(every operation rounded, float16 checked on every half value), whether it stays within the
Vulkan precision requirement of the builtin, malioc Vulkan cost of the builtin and of the
approximation on every GPU and type (same chain method as run.py), error curve for the plot
and on-device verification results.

Usage: python build_approx.py [--out ../../docs]
"""
import argparse
import json
import os

import numpy as np

from fit import all_halfs, eval_odd, odd_poly
from measure import measure

HERE = os.path.dirname(os.path.abspath(__file__))
PI = np.pi
INV_2PI = 0.159154943
# Vulkan "Precision and Operation of SPIR-V Instructions": sin/cos absolute error inside [-pi, pi]
SPEC_SIN = {"float": 2 ** -11, "half": 2 ** -7}

# rounded to the 9 digits printed into the code, so errors are those of the published code
_r9 = lambda c: [float(f"{v:.9g}") for v in c]
P7 = _r9(odd_poly(np.sin, 0, PI, 4)[0])
P5 = _r9(odd_poly(np.sin, 0, PI, 3)[0])
U7 = _r9(odd_poly(lambda u: np.sin(2 * PI * u), 0, 0.5, 4)[0])


def horner(c, var, x2):
    p = f"{c[-1]:.9g}"
    for ck in c[-2::-1]:
        p = f"({ck:.9g} + {x2} * {p})"
    return f"{var} * {p}"


def glsl_poly(name, c, reduce=False):
    """GLSL helper for type {T}; reduce: argument in radians, any x (period reduction)."""
    if reduce:
        body = (f"{{T}} u = x * {INV_2PI}; u = u - round(u); {{T}} u2 = u * u; "
                f"return {horner(c, 'u', 'u2')};")
    else:
        body = f"{{T}} x2 = x * x; return {horner(c, 'x', 'x2')};"
    return f"{{T}} {name}({{T}} x) {{ {body} }}\n"


def hlsl_poly(name, c, comment, reduce=False):
    if reduce:
        return (f"// {comment}\nfloat {name}(float x)\n{{\n    float u = x * {INV_2PI};   // x / 2pi\n"
                f"    u -= round(u);               // u in [-0.5, 0.5]\n    float u2 = u * u;\n"
                f"    return {horner(c, 'u', 'u2')};\n}}")
    return f"// {comment}\nfloat {name}(float x)\n{{\n    float x2 = x * x;\n    return {horner(c, 'x', 'x2')};\n}}"


def eval_any(c, x, dt):
    x = x.astype(dt)
    u = (x * dt(INV_2PI)).astype(dt)
    u = (u - np.round(u)).astype(dt)
    return eval_odd(c, u, dt)


APPROX = [
    {
        "id": "sin_fast7", "func": "sin", "name": "FastSin",
        "summary": "Полином 7-й степени.",
        "domain": [-PI, PI], "domain_text": "x ∈ [−π, π]", "plot": [-2 * PI, 2 * PI], "valid": [-PI, PI],
        "coeffs": P7, "reduce": False,
        "hlsl": hlsl_poly("FastSin", P7, "x in [-pi, pi], max error 2.5e-4"),
        "verified": [{"device": "Galaxy S21+", "gpu": "Mali-G78", "api": "Vulkan", "date": "2026-10-05",
                      "calls": 2048, "res": "2400×1080",
                      "builtin_ms": 139.66, "approx_ms": 70.02}],
    },
    {
        "id": "sin_fast5", "func": "sin", "name": "FastSin5",
        "summary": "Полином 5-й степени. Грубее, для анимаций.",
        "domain": [-PI, PI], "domain_text": "x ∈ [−π, π]", "plot": [-2 * PI, 2 * PI], "valid": [-PI, PI],
        "coeffs": P5, "reduce": False,
        "hlsl": hlsl_poly("FastSin5", P5, "x in [-pi, pi], max error 6.9e-3"),
        "verified": [],
    },
    {
        "id": "sin_any7", "func": "sin", "name": "FastSinAny",
        "summary": "Приведение к периоду + полином 7-й степени.",
        "domain": [-100, 100], "domain_text": "любой x (до |x| ≈ 1000)", "plot": [-4 * PI, 4 * PI], "valid": None,
        "coeffs": U7, "reduce": True,
        "hlsl": hlsl_poly("FastSinAny", U7, "any x, max error 2.6e-4 up to |x| = 100", reduce=True),
        "verified": [],
    },
]


def errors(a):
    f = (lambda c, x, dt: eval_any(c, x, dt)) if a["reduce"] else (lambda c, x, dt: eval_odd(c, x, dt))
    lo, hi = a["domain"]
    x = np.linspace(lo, hi, 400001)
    e32 = float(np.max(np.abs(f(a["coeffs"], x, np.float32).astype(np.float64) - np.sin(x.astype(np.float32).astype(np.float64)))))
    h = all_halfs(max(lo, -PI), min(hi, PI))
    e16 = float(np.max(np.abs(f(a["coeffs"], h, np.float16).astype(np.float64) - np.sin(h.astype(np.float64)))))
    plo, phi = a["plot"]
    xp = np.linspace(plo, phi, 401)
    ap = f(a["coeffs"], xp, np.float32).astype(np.float64)
    ep = [[round(float(x), 4), round(float(np.sin(x)), 5), round(float(y), 5)] for x, y in zip(xp, ap)]
    return e32, e16, ep


def costs(a):
    out = {}
    for v in ("float", "half", "float4", "half4"):
        T = "vec4" if v.endswith("4") else "float"
        pre = glsl_poly("approx_", a["coeffs"], a["reduce"]).replace("{T}", T)
        r = measure({"builtin": f"{a['func']}({{x}})", "approx": "approx_({x})"}, v, pre)
        arch = r["arch"]
        for g in arch:
            out.setdefault(g, {"arch": arch[g]})[v] = [r["builtin"][g], r["approx"][g]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "docs"))
    args = ap.parse_args()
    items = []
    for a in APPROX:
        e32, e16, ep = errors(a)
        print(f"{a['id']}: f32 {e32:.2e}  f16 {e16:.2e}", flush=True)
        items.append({
            "id": a["id"], "func": a["func"], "name": a["name"], "summary": a["summary"],
            "domain_text": a["domain_text"], "hlsl": a["hlsl"],
            "err": {"float": e32, "half": e16}, "spec": SPEC_SIN,
            "spec_text": "Допуск Vulkan для sin на [−π, π]: 4.9·10⁻⁴ (float), 7.8·10⁻³ (half)",
            "plot": ep, "plot_domain": a["plot"], "valid": a["valid"],
            "verified": a["verified"],
            "cost": costs(a),
        })
    path = os.path.join(args.out, "approx.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.MALI_APPROX = " + json.dumps(items, ensure_ascii=False) + ";\n")
    print("wrote", os.path.abspath(path))


if __name__ == "__main__":
    main()
