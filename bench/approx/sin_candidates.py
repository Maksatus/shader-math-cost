"""sin(x) candidates: minimax odd polynomials, with and without range reduction.
Fits coefficients, reports f32 / f16 max error and malioc Vulkan cost per architecture.

Usage: python sin_candidates.py [--variants float,half,float4,half4]
"""
import argparse
import numpy as np

from fit import all_halfs, eval_odd, horner_err, odd_poly
from measure import measure, table

PI = np.pi
SPEC = {"float": 2 ** -11, "half": 2 ** -7}  # Vulkan: sin abs error inside [-pi, pi]


def poly_glsl(name, c, T):
    """T name(T x): x * P(x*x) in Horner form."""
    p = f"{c[-1]:.9g}"
    for ck in c[-2::-1]:
        p = f"({p} * x2 + {ck:.9g})"
    return f"{T} {name}({T} x) {{ {T} x2 = x * x; return x * {p}; }}\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="float,half,float4,half4")
    args = ap.parse_args()

    fits = {}  # name -> (coeffs, domain of the polynomial argument)
    for terms in (3, 4, 5):
        fits[f"p{2 * terms - 1}"] = odd_poly(np.sin, 0, PI, terms)[0]
        fits[f"u{2 * terms - 1}"] = odd_poly(lambda u: np.sin(2 * PI * u), 0, 0.5, terms)[0]

    print("Errors (max abs, sin on [-pi, pi]); spec f32 %.1e, f16 %.1e" % (SPEC["float"], SPEC["half"]))
    h = all_halfs(-PI, PI)
    for name, c in fits.items():
        if name[0] != "p":
            continue
        e32 = horner_err(np.sin, c, -PI, PI, np.float32)
        e16 = np.max(np.abs(eval_odd(c, h, np.float16).astype(np.float64) - np.sin(h.astype(np.float64))))
        print(f"  {name}: f32 {e32:.2e}  f16 {e16:.2e}")

    for v in args.variants.split(","):
        T = "vec4" if v.endswith("4") else "float"
        pre = "".join(poly_glsl(n, c, T) for n, c in fits.items())
        exprs = {"sin (builtin)": "sin({x})"}
        for d in (5, 7, 9):
            exprs[f"p{d}  [-pi,pi] only"] = f"p{d}({{x}})"
        for d in (7, 9):
            exprs[f"u{d}  any x, round"] = f"u{d}({{x}} * 0.159154943 - round({{x}} * 0.159154943))"
            exprs[f"u{d}  any x, fract"] = f"u{d}(fract({{x}} * 0.159154943 + 0.5) - 0.5)"
        print(f"\n== {v}: cost in FMA, median per architecture (Vulkan)")
        table(measure(exprs, v, pre))


if __name__ == "__main__":
    main()
