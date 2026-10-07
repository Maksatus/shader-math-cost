"""Report v1 (plan item A1.6, decision D-09): report.csv from measurements.jsonl.

One row per shader variant file (pass x keywords x stage x API) with the heaviness
(A1.5) on every measured core and the maximum per architecture. Rows are ranked by the main
core (Mali-G78 if measured, else the first core, decision D-17): cycles of different cores are
not one scale (pipe widths differ between core classes), so a maximum over cores would just pick
the narrowest core. Fragment, vertex and compute shaders, and each API, are ranked separately.
"""
import csv
import json
import os
from collections import OrderedDict

from shaderopt.profile import score as heavy

MAIN_CORE = "Mali-G78"

ARCHS = ("Bifrost", "Valhall", "Arm 5th Generation")
ARCH_SHORT = {"Bifrost": "Bifrost", "Valhall": "Valhall", "Arm 5th Generation": "5th Gen"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def build_rows(records, fp16_threshold=heavy.FP16_THRESHOLD):
    """records (measurements.jsonl lines) -> (rows, cores, failures)."""
    rows, cores, failures = OrderedDict(), OrderedDict(), []
    for rec in records:
        if not rec.get("ok", True):
            failures.append(rec)
            continue
        cores[rec["core"]] = rec["arch"]
        key = (rec["file"], rec["api"])
        row = rows.setdefault(key, {
            "file": rec["file"], "shader": rec.get("shader") or os.path.splitext(os.path.basename(rec["file"]))[0],
            "pass": rec.get("pass", ""), "keywords": rec.get("keywords", []), "stage": rec["stage"],
            "api": rec["api"], "scores": {}})
        row["scores"][rec["core"]] = heavy.score(rec, fp16_threshold)
    main = MAIN_CORE if MAIN_CORE in cores else next(iter(cores), None)
    for row in rows.values():
        sc = row["scores"]
        core = main if main in sc else next(iter(sc))
        row["main"] = {"core": core, "cycles": sc[core]["cycles"]}
        row["arch"] = {}
        for a in ARCHS:
            vals = [s["cycles"] for c, s in sc.items() if cores[c] == a]
            row["arch"][ARCH_SHORT[a]] = max(vals) if vals else None
        row["flags"] = [f for f in heavy.FLAGS if any(f in s["flags"] for s in row["scores"].values())]
        row["work_regs"] = max(s["work_regs"] for s in row["scores"].values())
        fp = [s["fp16_pct"] for s in row["scores"].values() if s["fp16_pct"] is not None]
        row["fp16_pct"] = min(fp) if fp else None
    ordered = sorted(rows.values(), key=lambda r: (r["stage"], r["api"], -r["main"]["cycles"]))
    for stage in ("fragment", "vertex", "compute"):
        for api in sorted({r["api"] for r in ordered}):
            for i, r in enumerate((r for r in ordered if r["stage"] == stage and r["api"] == api), 1):
                r["rank"] = i
    ordered.sort(key=lambda r: (r["stage"], -r["main"]["cycles"]))
    return ordered, list(cores.items()), failures


def write_csv(rows, cores, path):
    cols = (["stage", "rank", "shader", "pass", "keywords", "api", "file", "main_cycles", "main_core"]
            + [f"{ARCH_SHORT[a]}_cycles" for a in ARCHS]
            + [f"{c}_{k}" for c, _ in cores for k in ("cycles", "bound", "path")]
            + ["work_regs", "fp16_pct", "flags"])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            line = [r["stage"], r["rank"], r["shader"], r["pass"], " ".join(r["keywords"]), r["api"], r["file"],
                    r["main"]["cycles"], r["main"]["core"]] + [r["arch"][ARCH_SHORT[a]] for a in ARCHS]
            for c, _ in cores:
                s = r["scores"].get(c)
                line += [s["cycles"], "+".join(s["bound"]), s["path"]] if s else ["", "", ""]
            line += [r["work_regs"], r["fp16_pct"], " ".join(r["flags"])]
            w.writerow(["" if v is None else v for v in line])


def run(folder, out=None, fp16_threshold=heavy.FP16_THRESHOLD):
    """measurements.jsonl in `folder` -> report.csv in `out` (default: folder). The HTML view of shaders is the
    "project shaders" tab of the frame report (cost --materials)."""
    out = out or folder
    records = load(os.path.join(folder, "measurements.jsonl"))
    rows, cores, failures = build_rows(records, fp16_threshold)
    os.makedirs(out, exist_ok=True)
    write_csv(rows, cores, os.path.join(out, "report.csv"))
    return rows, cores, failures

