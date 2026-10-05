"""Measure the per-call cost of arbitrary GLSL expressions with malioc (Vulkan).

Same method as run.py: chain of N steps x = (expr) + w_i, slope between chain
lengths 8 and 24 minus the slope of the bare '+ w_i' chain, in units of one FMA
(slope of the 'mad' chain on the same GPU). Cost = bottleneck arithmetic pipe.
"""
import concurrent.futures as cf
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from run import MALIOC_NEW, MALIOC_OLD, compile_one, list_gpus, slope, spills
from shadergen import build

PAIRS = [(8, 24), (8, 16), (4, 8)]
TYPES = {"float": ("highp", "float"), "half": ("mediump", "float"),
         "float4": ("highp", "vec4"), "half4": ("mediump", "vec4")}


def vulkan_gpus(legacy=True):
    gpus = [(MALIOC_NEW, n, a) for n, a, apis in list_gpus(MALIOC_NEW) if "Vulkan" in apis]
    if legacy and os.path.exists(MALIOC_OLD):
        known = {g[1] for g in gpus}
        gpus += [(MALIOC_OLD, n, a) for n, a, apis in list_gpus(MALIOC_OLD)
                 if n not in known and a == "Midgard" and "Vulkan" in apis]
    return gpus


def _cost(malioc, core, prec, t, expr, prelude, combine):
    for n1, n2 in PAIRS:
        src = lambda e, n, c, pre="": build("vulkan", prec, t, e, n, c, pre)
        r = [compile_one(malioc, "vulkan", core, src(expr, n, combine, prelude)) for n in (n1, n2)]
        b = [compile_one(malioc, "vulkan", core, src("{x}", n, True)) for n in (n1, n2)] if combine else []
        if not all(x["ok"] for x in r + b):
            return None
        if any(spills(x) for x in r + b) and (n1, n2) != PAIRS[-1]:
            continue
        s = slope(r[0], r[1], n1, n2)
        if b:
            sb = slope(b[0], b[1], n1, n2)
            s = {k: s[k] - sb[k] for k in s}
        s.pop("texture", None)
        return {k: max(0.0, v) for k, v in s.items()}
    return None


def measure(exprs, variant="float", prelude="", gpus=None, jobs=16):
    """exprs: {name: glsl step expr with {x},{a},{b}}. Returns {name: {gpu: rel_fma}}
    plus 'arch' map. Expressions are combined with '+ w_i' like run.py."""
    prec, t = TYPES[variant]
    gpus = gpus or vulkan_gpus()
    tasks = {}
    with cf.ThreadPoolExecutor(jobs) as ex:
        for malioc, core, arch in gpus:
            tasks[("__mad__", core)] = ex.submit(_cost, malioc, core, "highp", "float", "{x} * {a} + {b}", "", False)
            for name, e in exprs.items():
                tasks[(name, core)] = ex.submit(_cost, malioc, core, prec, t, e, prelude, True)
        res = {k: f.result() for k, f in tasks.items()}
    out = {name: {} for name in exprs}
    for malioc, core, arch in gpus:
        unit = max(res[("__mad__", core)].values())
        for name in exprs:
            s = res[(name, core)]
            out[name][core] = round(max(s.values()) / unit, 2) if s else None
    out["arch"] = {core: arch for _, core, arch in gpus}
    return out


def table(res, names=None, base=None):
    """Print per-architecture medians; base: name to show ratios against."""
    import statistics
    names = names or [n for n in res if n != "arch"]
    archs = list(dict.fromkeys(res["arch"].values()))
    print(f"{'':28}" + "".join(f"{a[:12]:>13}" for a in archs))
    for n in names:
        cells = []
        for a in archs:
            v = [c for g, c in res[n].items() if res["arch"][g] == a and c is not None]
            cells.append(f"{statistics.median(v):>13.2f}" if v else f"{'-':>13}")
        print(f"{n[:28]:28}" + "".join(cells))
