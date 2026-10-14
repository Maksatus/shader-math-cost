"""Builds docs/approx.js: approximations of table functions with exact errors (check.py), hazards and malioc costs.

check.py results are cached in bench/.cache/approx_check (a full float32 check takes minutes per approximation).

Usage: python build_approx.py [--out ../../docs] [--keep-cost] [--fast]
"""
import argparse
import hashlib
import inspect
import json
import os
import sys
import time

import numpy as np

import check
import fit
from fit import eval_odd, odd_poly
from measure import measure

HERE = os.path.dirname(os.path.abspath(__file__))
PI = np.pi
INV_2PI = 0.159154943
# Vulkan spec: sin/cos absolute error inside [-pi, pi]
SPEC_SIN = {"float": 2 ** -11, "half": 2 ** -7}
CACHE = os.path.join(HERE, "..", ".cache", "approx_check")
REFS = {"sin": {"ref": np.sin, "range": [-1.0, 1.0], "spec": SPEC_SIN, "beyond": 1e5,
                "probes": [4.0, 2 * PI, 10.0, 9491.0, 1e5]}}
FAST_STRIDE = 61

# rounded to the 9 printed digits: errors are those of the published code
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
    """GLSL helper for type {T}; reduce: period reduction for any x."""
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


def eval_any(c, x, dt, fma=False):
    x = x.astype(dt)
    u = (x * dt(INV_2PI)).astype(dt)
    u = (u - np.round(u)).astype(dt)
    return eval_odd(c, u, dt, fma)


def approx_fn(a):
    """(x, dt, fma) -> approximation of a in type dt, every operation rounded."""
    ev = eval_any if a["reduce"] else eval_odd
    return lambda x, dt, fma=False: ev(a["coeffs"], x, dt, fma)


def num(v, d=2):
    """d significant digits, small values as 2.5·10⁻⁴, minus sign as in the texts."""
    if v and abs(v) < 1e-3:
        m, e = f"{abs(v):.{d - 1}e}".split("e")
        t = f"{m}·10" + "".join("⁻⁰¹²³⁴⁵⁶⁷⁸⁹"["-0123456789".index(ch)] for ch in str(int(e)))
    else:
        t = f"{float(f'{abs(v):.{d}g}'):g}"
    return ("−" if v < 0 else "") + t


def cnt(n):
    return f"{n:,}".replace(",", " ") if n >= 10000 else str(n)


def over1(y):
    """1 + excess to 2 significant digits: 1.00025, 1.0039."""
    return f"{1 + float(f'{y - 1:.2g}'):g}"


# texts are strings or functions of facts(): numbers are never typed by hand
APPROX = [
    {
        "id": "sin_fast7", "func": "sin", "name": "FastSin",
        "summary": "Полином 7-й степени.",
        "domain": [-PI, PI], "domain_text": "x ∈ [−π, π]", "plot": [-2 * PI, 2 * PI], "valid": [-PI, PI],
        "coeffs": P7, "reduce": False,
        "hlsl": hlsl_poly("FastSin", P7, "x in [-pi, pi], max error 2.5e-4"),
        "hazards": [
            {"title": "Выход за 1",
             "text": lambda v: f"максимум {over1(v['ymax32'])} при |x| ∈ [{num(v['over_x32'][0], 4)}, "
                               f"{num(v['over_x32'][1], 4)}], {cnt(v['over32'])} float на каждый знак; "
                               f"в half — {over1(v['ymax16'])}. sqrt(1 − s²), acos(s), asin(s) дают NaN",
             "fix": lambda v: f"saturate у потребителя (бесплатно) или коэффициенты × 1/{over1(v['ymax32'])}"},
            {"title": "Неверный знак у ±π",
             "text": lambda v: f"FastSin(π) = {num(v['y_hi32'])}; у {cnt(v['sign32'])} float в пределах "
                               f"{num(v['sign_d32'], 3)} от ±π знак противоположный "
                               + (f"(в half — у {cnt(v['sign16'])})" if v["sign16"] else "(в half таких нет)")
                               + ". Ломает step(0, sin x), sign, clip",
             "fix": "не сравнивать результат с нулём у краёв или брать sin"},
            {"title": "Расходится вне [−π, π]",
             "text": lambda v: f"допуск превышен уже при |x| = {num(v['exceed32'], 5)}; ошибка "
                               f"{num(abs(v['probe32'][0]))} при x = 4, {num(abs(v['probe32'][1]))} при 2π, "
                               f"{num(abs(v['probe32'][2]))} при x = 10",
             "fix": "гарантировать диапазон или брать FastSinAny"},
        ],
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
        "hazards": [
            {"title": "Выход за 1",
             "text": lambda v: f"максимум {over1(v['ymax32'])} при |x| ∈ [{num(v['over_x32'][0], 4)}, "
                               f"{num(v['over_x32'][1], 4)}]; в half — {over1(v['ymax16'])}. "
                               "sqrt(1 − s²), acos(s), asin(s) дают NaN",
             "fix": "saturate у потребителя (бесплатно)"},
            {"title": "Расходится вне [−π, π]",
             "text": lambda v: f"ошибка {num(abs(v['probe32'][0]))} при x = 4, {num(abs(v['probe32'][1]))} при 2π, "
                               f"{num(abs(v['probe32'][2]))} при x = 10",
             "fix": "гарантировать диапазон или брать FastSinAny"},
        ],
        "verified": [],
    },
    {
        "id": "sin_any7", "func": "sin", "name": "FastSinAny",
        "summary": "Приведение к периоду + полином 7-й степени.",
        "domain": [-100, 100], "domain16": [-PI, PI],
        "domain_text": lambda v: f"|x| < {num(v['exceed32'], 4)} (half: |x| < {num(v['exceed16'])})",
        "plot": [-4 * PI, 4 * PI], "valid": None,
        "coeffs": U7, "reduce": True,
        "hlsl": hlsl_poly("FastSinAny", U7, "any x, max error 2.6e-4 up to |x| = 100", reduce=True),
        "hazards": [
            {"title": lambda v: f"Только при |x| < {num(v['exceed32'], 4)}",
             "text": lambda v: "дальше ошибка больше допуска: x / 2π во float теряет точность. "
                               f"В half — уже при |x| = {num(v['exceed16'], 3)}",
             "fix": "держать аргумент малым, например frac() у времени до умножения на 2π"},
            {"title": "Выход за 1 и неверный знак у x = kπ",
             "text": lambda v: f"тот же полином, что у FastSin: максимум {over1(v['ymax32'])}; "
                               f"на [{num(v['lo32'])}, {num(v['hi32'])}] у {cnt(v['sign32'])} float знак противоположный",
             "fix": "saturate у потребителя; не сравнивать результат с нулём"},
        ],
        "verified": [],
    },
]


def run_check(a, dt, stride=1):
    """check.check() of a in dt; full checks are cached by everything the result depends on."""
    r, v = REFS[a["func"]], "half" if dt is np.float16 else "float"
    args = {"domain": list(a.get("domain16", a["domain"]) if dt is np.float16 else a["domain"]),
            "vrange": r["range"], "tol": r["spec"][v], "beyond": r["beyond"], "probe_x": r["probes"]}
    src = inspect.getsource(check) + inspect.getsource(fit) + inspect.getsource(eval_any) + inspect.getsource(approx_fn)
    h = hashlib.sha256(json.dumps([a["func"], a["coeffs"], a["reduce"], INV_2PI, v, args, src]).encode()).hexdigest()
    path = os.path.join(CACHE, f"{a['id']}_{v}_{h[:20]}.json")
    if stride == 1 and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    c = check.check(approx_fn(a), r["ref"], dt=dt, stride=stride, **args)
    if stride == 1:
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(c, f)
    return c


def facts(c32, c16):
    """Numbers for the card and the hazard texts (no fma), key + '32' / '16'."""
    v = {}
    for t, c in (("32", c32), ("16", c16)):
        s, (lo, hi) = c["fma"]["false"], c["domain"]
        over = s["over"]["list"]
        v.update({f"err{t}": s["abs"][0], f"ymax{t}": s["ymax"][0], f"lo{t}": lo, f"hi{t}": hi,
                  f"over{t}": s["over"]["count"], f"over_x{t}": [over[0]["x"][0], over[-1]["x"][1]] if over else None,
                  f"sign{t}": s["sign"]["count"],
                  f"sign_d{t}": max((min(q["x"][1] - lo, hi - q["x"][0]) for q in s["sign"]["list"]), default=0.0),
                  f"y_hi{t}": next(p["y"] for p in c["specials"] if p["name"] == "hi"),
                  f"exceed{t}": c["exceed"]["fma"]["false"][1], f"probe{t}": [p["err"] for p in c["probes"]]})
    return v


def plot(a):
    lo, hi = a["plot"]
    xp = np.linspace(lo, hi, 401)
    ap = approx_fn(a)(xp, np.float32).astype(np.float64)
    return [[round(float(x), 4), round(float(np.sin(x)), 5), round(float(y), 5)] for x, y in zip(xp, ap)]


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
    ap.add_argument("--keep-cost", action="store_true", help="malioc costs from the existing approx.js (no malioc)")
    ap.add_argument("--fast", action="store_true",
                    help=f"float32 on 1 of every {FAST_STRIDE} values; prints the results, approx.js is not written")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    path = os.path.join(args.out, "approx.js")
    old = {}
    if args.keep_cost and not args.fast:
        with open(path, encoding="utf-8") as f:
            old = {a["id"]: a for a in json.loads(f.read().split("=", 1)[1].rstrip().rstrip(";"))}
    items = []
    for a in APPROX:
        t = time.perf_counter()
        v = facts(run_check(a, np.float32, FAST_STRIDE if args.fast else 1), run_check(a, np.float16))
        val = lambda f: f(v) if callable(f) else f
        hazards = [{k: val(x) for k, x in z.items()} for z in a.get("hazards", [])]
        print(f"{a['id']}: f32 {v['err32']:.5g}  f16 {v['err16']:.5g}  ({time.perf_counter() - t:.1f} s)", flush=True)
        if args.fast:
            print(f"  domain: {val(a['domain_text'])}")
            for z in hazards:
                print(f"  ⚠ {z['title']}: {z['text']}. Как обойти: {z['fix']}.")
            continue
        items.append({
            "id": a["id"], "func": a["func"], "name": a["name"], "summary": a["summary"],
            "domain_text": val(a["domain_text"]), "hlsl": a["hlsl"],
            "err": {"float": v["err32"], "half": v["err16"]}, "spec": SPEC_SIN,
            "spec_text": "Допуск Vulkan для sin на [−π, π]: 4.9·10⁻⁴ (float), 7.8·10⁻³ (half)",
            "plot": plot(a), "plot_domain": a["plot"], "valid": a["valid"],
            "hazards": hazards, "verified": a["verified"],
            "cost": old[a["id"]]["cost"] if args.keep_cost else costs(a),
        })
    if args.fast:
        print(f"--fast: float32 on 1 of every {FAST_STRIDE} values, approx.js not written")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.MALI_APPROX = " + json.dumps(items, ensure_ascii=False) + ";\n")
    print("wrote", os.path.abspath(path))


if __name__ == "__main__":
    main()
