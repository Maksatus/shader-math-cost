"""Packs docs/mali_math_cost.csv into docs/data.js so that index.html
works when opened straight from disk (file:// cannot fetch() a CSV).
Also writes docs/summary_<api>.csv: function x variant, median per architecture.

Usage: python build_site.py [site_dir]
"""
import csv
import json
import os
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
res = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "docs")
src = os.path.join(res, "mali_math_cost.csv")

with open(src, encoding="utf-8") as f:
    text = f.read()
with open(os.path.join(res, "data.js"), "w", encoding="utf-8") as f:
    f.write("window.MALI_CSV = " + json.dumps(text, ensure_ascii=False) + ";\n")

rows = list(csv.DictReader(text.splitlines()))
GROUP_ORDER = ["Bifrost", "Valhall", "Arm 5th Generation"]
archs = [a for a in GROUP_ORDER if any(r["arch"] == a for r in rows)]
funcs = list(dict.fromkeys(r["func"] for r in rows))
variants = ["float", "half", "float4", "half4"]

for api in ("GLES", "Vulkan"):
    g = defaultdict(list)
    meta = {}
    for r in rows:
        if r["api"] != api or r["rel_fma"] == "":
            continue
        g[(r["func"], r["variant"], r["arch"])].append(float(r["rel_fma"]))
        g[(r["func"], r["variant"], "*")].append(float(r["rel_fma"]))
        meta[r["func"]] = (r["category"], r["hlsl"])
    path = os.path.join(res, f"summary_{api.lower()}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["category", "func", "hlsl", "variant"] + [f"median {a}" for a in archs] + ["median all", "min all", "max all"])
        for fn in funcs:
            for v in variants:
                allv = g.get((fn, v, "*"))
                if not allv:
                    continue
                w.writerow([meta[fn][0], fn, meta[fn][1], v]
                           + [round(statistics.median(g[(fn, v, a)]), 2) if g.get((fn, v, a)) else "" for a in archs]
                           + [round(statistics.median(allv), 2), round(min(allv), 2), round(max(allv), 2)])
    print("wrote", os.path.abspath(path))
print("wrote", os.path.abspath(os.path.join(res, "data.js")))
