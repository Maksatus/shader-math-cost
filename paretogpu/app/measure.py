"""Measure every shader in a folder with malioc on one or more cores (plan items A1.1, A1.3, A1.4).

Stage comes from the extension: .vert / .frag / .comp (GLSL, compiled for `api`),
.vert.spv / .frag.spv / .comp.spv (binary SPIR-V, always Vulkan). Subfolders are included.
If a folder has manifest.json (from adapters/unity/split.py), shader, pass and
keywords are taken from it.

Output:
  measurements.jsonl - one line per shader file x core, all variants (as mali.parse());
  measurements.csv   - one row per shader file x core x variant (main / position / varying);
                       vertex shaders get one more row, "position+varying", with the sums (IDVS).
Cycles per pipe on the longest / shortest path and in total; the longest path
is empty when malioc reports N/A (dynamic loops).
"""
import csv
import json
import os

from paretogpu.adapters import malioc as mali
from paretogpu.app.engine import Engine
from paretogpu.app.rules import MeasureRule
from paretogpu.core import pricing as heavy
from paretogpu.core import ranking
from paretogpu.model.cores import MAIN_CORE
from paretogpu.views import tables
from paretogpu.views.reporter import CONSOLE


def find_shaders(folder):
    """[(path, relative path, manifest entry or {})], sorted by relative path."""
    out = []
    for root, _, files in os.walk(folder):
        man = {}
        if "manifest.json" in files:
            with open(os.path.join(root, "manifest.json"), encoding="utf-8") as f:
                man = {m["file"]: m for m in json.load(f)}
        for fn in files:
            if mali.stage_of(fn) in ("vertex", "fragment", "compute"):
                path = os.path.join(root, fn)
                out.append((path, os.path.relpath(path, folder).replace("\\", "/"), man.get(fn, {})))
    return sorted(out, key=lambda t: t[1])


def run(folder, cores=(MAIN_CORE,), api="gles", out=None, jobs=None, rep=CONSOLE):
    """Measure the folder on every core; returns (records, failures). Writes the CSV and JSONL into out."""
    if isinstance(cores, str):
        cores = [cores]
    out = out or folder
    shaders = find_shaders(folder)
    info = {rel: inf for _, rel, inf in shaders}
    tasks = [(rel, core) for _, rel, _ in shaders for core in cores]
    results = Engine(rep, jobs).get(MeasureRule(folder, api, info), tasks)
    records, failures = [], []
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "measurements.jsonl"), "w", encoding="utf-8", newline="\n") as fj, \
            open(os.path.join(out, "measurements.csv"), "w", encoding="utf-8", newline="") as fc:
        w = csv.DictWriter(fc, fieldnames=tables.COLUMNS)
        w.writeheader()
        for rel, core in tasks:
            r = results[(rel, core)]
            if not r["ok"]:
                failures.append((f"{rel} [{core}]", r["error"]))
                fj.write(json.dumps(r, ensure_ascii=False) + "\n")
                continue
            rec = {k: v for k, v in r.items() if k != "cached"}
            records.append((rec, r.get("cached", False)))
            fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
            w.writerows(tables.csv_rows(rel, info[rel], r))
    return records, failures


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def report(folder, out=None, fp16_threshold=heavy.FP16_THRESHOLD):
    """measurements.jsonl in `folder` -> report.csv in `out` (default: folder). The HTML view of shaders is the
    "project shaders" tab of the frame report (cost --materials)."""
    out = out or folder
    records = load(os.path.join(folder, "measurements.jsonl"))
    rows, cores, failures = ranking.build_rows(records, fp16_threshold)
    os.makedirs(out, exist_ok=True)
    tables.write_report_csv(rows, cores, os.path.join(out, "report.csv"))
    return rows, cores, failures
