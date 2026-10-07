"""Mali shader math cost benchmark.

Compiles chained fragment shaders with malioc for every Mali GPU on both
OpenGL ES and Vulkan, then derives the per-call cost of each function.

Per-call cost = slope of arithmetic cycles between chain lengths N1 and N2
(8 -> 24; falls back to 8 -> 16 or 4 -> 8 if the shader spills registers), minus the slope of the same chain without the function (only the '+ w_i'
adds). The slope cancels fixed shader overhead, which matters on Bifrost.
Cost is taken on the bottleneck arithmetic pipe (FMA / CVT / SFU run in
parallel on Valhall and 5th Gen; Bifrost reports one pipe).

malioc results are cached in .cache/ by shader source. Results that look like a
corrupted cache (a strongly negative per-pipe cost, or a different FMA unit in
GLES and Vulkan on the same GPU) are reported in errors.txt: delete .cache/ and rerun.

Usage: python run.py [--jobs 16] [--gpus Mali-G57,Mali-G52] [--out ../docs]
"""
import argparse
import concurrent.futures as cf
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from shadergen import build
from functions import FUNCS, HLSL_EXPR, glsl_type, glsl_expr, hlsl_type, prelude
from shaderopt import mali
from shaderopt import progress

MALIOC_NEW = mali.MALIOC
MALIOC_VERSION = ".".join(mali.version(MALIOC_NEW).split(".")[:2])  # the CSV column: "2026.5"

PAIRS = [(8, 24), (8, 16), (4, 8)]  # chain lengths (N1, N2), tried in order
VARIANTS = [  # name, precision, vector size
    ("float",  "highp",   1),
    ("half",   "mediump", 1),
    ("float4", "highp",   4),
    ("half4",  "mediump", 4),
]
APIS = ["gles", "vulkan"]
ARITH_PIPES = ("arith_fma", "arith_cvt", "arith_sfu", "arithmetic")

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".cache")


def list_gpus(malioc):
    return mali.list_cores(malioc)


def compile_one(malioc, api, core, src):
    return mali.compile(src, core, api, malioc=malioc, cache=CACHE)


def arith(c):
    return {p: c[p] for p in ARITH_PIPES + ("texture",) if p in c}


def slope(r1, r2, n1, n2, key="cycles"):
    a, b = arith(r1[key]), arith(r2[key])
    return {p: (b[p] - a[p]) / (n2 - n1) for p in a}


spills = mali.spills


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--gpus", default="")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "docs"))
    args = ap.parse_args()

    gpus = [(MALIOC_NEW, n, a, apis) for n, a, apis in list_gpus(MALIOC_NEW)]
    if args.gpus:
        want = set(args.gpus.split(","))
        gpus = [g for g in gpus if g[1] in want]
    print(f"{len(gpus)} GPUs: " + ", ".join(g[1] for g in gpus), flush=True)

    plan = []  # one entry per measured (gpu, api, variant, func)
    for malioc, core, arch, apis in gpus:
        for api in APIS:
            if ("Vulkan" if api == "vulkan" else "OpenGL ES") not in apis:
                continue
            for vname, prec, dim in VARIANTS:
                for fid, hlsl, expr, cat, kind, combine, extra, note in FUNCS:
                    if kind in ("int", "uint") and prec == "mediump":
                        continue  # mediump ints give unstable results; Unity rarely uses min16int
                    t = glsl_type(kind, dim)
                    e = glsl_expr(expr, t)
                    plan.append(dict(
                        malioc=malioc, api=api, core=core, arch=arch, variant=vname, prec=prec,
                        fid=fid, hlsl=hlsl, glsl=e.format(x="x", a="a", b="b", s="s", T=t),
                        category=cat, note=note, t=t, expr=e, combine=combine, extra=extra, kind=kind,
                        prelude=prelude(fid, t, prec), pair=0))

    results = {}  # (malioc, api, core, src) -> compile result

    def keys_for(p, n):
        mk = lambda e, c, pre="": (p["malioc"], p["api"], p["core"],
                                   build(p["api"], p["prec"], p["t"], e, n, c, pre))
        return (mk(p["expr"], p["combine"], p["prelude"]),
                mk("{x}", True) if p["combine"] else None)

    def compile_all(keys):
        keys = [k for k in dict.fromkeys(keys) if k not in results]
        print(f"  compiling {len(keys)} shaders", flush=True)
        progress.phase("bench_compile", len(keys))
        tick = progress.counter(len(keys))
        done = 0
        with cf.ThreadPoolExecutor(args.jobs) as ex:
            futs = {ex.submit(compile_one, *k): k for k in keys}
            for fut in cf.as_completed(futs):
                results[futs[fut]] = fut.result()
                done += 1
                tick()
                if done % 5000 == 0:
                    print(f"  {done}/{len(keys)}", flush=True)

    print(f"{len(plan)} measurements", flush=True)
    # Measure with the first chain-length pair whose shaders do not spill registers.
    rows, errors = [], []
    slopes = {}
    todo = plan
    while todo:
        need = []
        for p in todo:
            n1, n2 = PAIRS[p["pair"]]
            for n in (1, n1, n2):
                need += [k for k in keys_for(p, n) if k]
        compile_all(need)
        retry = []
        for p in todo:
            n1, n2 = PAIRS[p["pair"]]
            (f1, b1), (f2, b2), (fs, _) = keys_for(p, n1), keys_for(p, n2), keys_for(p, 1)
            rs_all = [results[k] for k in (f1, f2, fs, b1, b2) if k]
            if not all(r["ok"] for r in rs_all):
                errors.append((p["core"], p["api"], p["variant"], p["fid"],
                               next(r["error"] for r in rs_all if not r["ok"])))
                continue
            if any(spills(r) for r in rs_all) and p["pair"] + 1 < len(PAIRS):
                p["pair"] += 1
                retry.append(p)
                continue
            if any(v is None for r in rs_all for c in (r["cycles"], r["short"]) for v in c.values()):
                errors.append((p["core"], p["api"], p["variant"], p["fid"], "malioc returned no cycle count"))
                continue
            s = slope(results[f1], results[f2], n1, n2)
            ss = slope(results[f1], results[f2], n1, n2, "short")
            if b1:
                sb = slope(results[b1], results[b2], n1, n2)
                s = {k: s[k] - sb[k] for k in s}
                ss = {k: ss[k] - sb[k] for k in ss}
            p["short"] = ss
            slopes[(p["core"], p["api"], p["variant"], p["fid"])] = s
            p["slope"], p["r2"], p["rs"] = s, results[f2], results[fs]
            p["spill"] = any(spills(r) for r in rs_all)
        if retry:
            print(f"  {len(retry)} measurements spill registers, retrying with shorter chains", flush=True)
        todo = retry

    # Corrupted cache entries (results of another shader) showed up as a different FMA unit
    # in GLES and Vulkan on one GPU and as strongly negative per-pipe costs.
    warnings = []
    for core in dict.fromkeys(p["core"] for p in plan):
        u = {api: max(v for k, v in slopes[(core, api, "float", "mad")].items() if k != "texture")
             for api in APIS if (core, api, "float", "mad") in slopes}
        if len(u) == 2 and abs(u["gles"] - u["vulkan"]) > 0.2 * max(u.values()):
            warnings.append(f"{core}: FMA unit differs, GLES {u['gles']:.5f} vs Vulkan {u['vulkan']:.5f} cycles")

    for p in plan:
        if "slope" not in p:
            continue
        s, sh = dict(p["slope"]), dict(p["short"])
        if p["extra"]:
            add = slopes[(p["core"], p["api"], p["variant"], "add")]
            # 'add' of the variant type; extra adds are on the same type
            s = {k: s[k] - p["extra"] * add[k] for k in s}
            sh = {k: sh[k] - p["extra"] * add[k] for k in sh}
        unit = slopes.get((p["core"], p["api"], "float", "mad"), {})
        unit_c = max(v for k, v in unit.items() if k != "texture") if unit else 0
        # a function may move a little work off a pipe vs the baseline, but not several FMAs
        neg = min((v, k) for k, v in s.items() if k != "texture")
        if unit_c and neg[0] < -4 * unit_c:
            warnings.append(f"{p['core']} | {p['api']} | {p['variant']} | {p['fid']}: "
                            f"{neg[1]} = {neg[0] / unit_c:.1f} FMA per call")
        s = {k: max(0.0, v) for k, v in s.items()}
        short = max([max(0.0, v) for k, v in sh.items() if k != "texture"] or [0.0])
        tex = s.pop("texture", 0.0)
        alu = max(s.values()) if s else 0.0
        # the texture pipe runs in parallel with arithmetic: like FMA/CVT/SFU, the busiest pipe is the cost
        cyc = max(alu, tex)
        bound = ("TEX" if tex >= alu else max(s, key=s.get)) if cyc > 1e-9 else "-"
        ref = slopes.get((p["core"], p["api"], "float", "tex2D"), {}).get("texture", 0)
        props2, props1 = p["r2"]["props"], p["rs"]["props"]
        rows.append({
            "api": "GLES" if p["api"] == "gles" else "Vulkan",
            "gpu": p["core"], "arch": p["arch"],
            "malioc": MALIOC_VERSION,
            "variant": p["variant"], "type": hlsl_type(p["prec"], p["t"]),
            "category": p["category"], "func": p["fid"], "hlsl": p["hlsl"],
            "hlsl_expr": HLSL_EXPR[p["fid"]] or "— (нет в HLSL)",
            "glsl": p["glsl"], "note": p["note"],
            "cycles": round(cyc, 5),
            "rel_fma": round(cyc / unit_c, 3) if unit_c else "",
            "unit": "FMA",
            # cost on the shortest path (a skipped branch); empty unless cheaper than the longest path
            "rel_short": round(short / unit_c, 3)
                         if unit_c and p["kind"] != "tex" and short < cyc - 1e-6 else "",
            # texture rows: arithmetic for the coordinates (FMA) and texture time in plain tex2D samples
            "alu_fma": round(alu / unit_c, 3) if p["kind"] == "tex" and unit_c else "",
            "rel_tex": round(tex / ref, 3) if p["kind"] == "tex" and ref else "",
            "fma": round(s["arith_fma"], 5) if "arith_fma" in s else "",
            "cvt": round(s["arith_cvt"], 5) if "arith_cvt" in s else "",
            "sfu": round(s["arith_sfu"], 5) if "arith_sfu" in s else "",
            "bound": {"arith_fma": "FMA", "arith_cvt": "CVT", "arith_sfu": "SFU",
                      "arithmetic": "A"}.get(bound, bound),
            "fp16_pct": props2.get("fp16_arithmetic", ""),
            "work_regs_1call": props1.get("work_registers_used", ""),
            "uniform_regs_1call": props1.get("uniform_registers_used", ""),
            "chain": "%d-%d" % PAIRS[p["pair"]],
            "spill": p["spill"],
            "driver": p["r2"]["driver"],
        })

    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "mali_math_cost.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {os.path.abspath(out)}")
    epath = os.path.join(HERE, "errors.txt")
    if errors or warnings:
        with open(epath, "w", encoding="utf-8") as f:
            for e in errors:
                f.write(" | ".join(map(str, e[:4])) + "\n" + e[4] + "\n\n")
            for w in warnings:
                f.write("SUSPICIOUS " + w + "\n")
        if errors:
            print(f"{len(errors)} failed measurements -> {epath}")
        if warnings:
            print(f"{len(warnings)} suspicious results, the cache may be corrupted: delete {CACHE} and rerun -> {epath}")
    elif os.path.exists(epath):
        os.remove(epath)


if __name__ == "__main__":
    main()
