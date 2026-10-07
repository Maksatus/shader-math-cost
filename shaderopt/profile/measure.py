"""Measure every shader in a folder with malioc on one or more cores (plan items A1.1, A1.3, A1.4).

Stage comes from the extension: .vert / .frag / .comp (GLSL, compiled for `api`),
.vert.spv / .frag.spv / .comp.spv (binary SPIR-V, always Vulkan). Subfolders are included.
If a folder has manifest.json (from corpus/split_unity.py), shader, pass and
keywords are taken from it.

Output:
  measurements.jsonl - one line per shader file x core, all variants (as mali.parse());
  measurements.csv   - one row per shader file x core x variant (main / position / varying);
                       vertex shaders get one more row, "position+varying", with the sums (IDVS).
Cycles per pipe on the longest / shortest path and in total; the longest path
is empty when malioc reports N/A (dynamic loops).
"""
import concurrent.futures as cf
import csv
import json
import os

from shaderopt import mali
from shaderopt import progress
from shaderopt.profile.score import add_cycles

PIPES = ("arith", "fma", "cvt", "sfu", "ls", "v", "t")
PATHS = ("longest", "shortest", "total")
COLUMNS = (["file", "shader", "pass", "keywords", "stage", "variant",
            "core", "arch", "api", "driver", "malioc", "bound"]
           + [f"{p}_{pipe}" for p in PATHS for pipe in PIPES]
           + ["work_regs", "uniform_regs", "occupancy", "spilling", "spill_bytes",
              "fp16_pct", "uniform_computation", "source_sha1"])
IDVS_SUM = "position+varying"


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


def api_of(path, api):
    return "vulkan" if path.endswith(mali.SPIRV_EXT) else api


def measure_one(path, core, api):
    """mali.measure() of one file; an error (unreadable file, malioc not found) fails this file, not the run."""
    try:
        return mali.measure(mali.read_source(path), core, api_of(path, api), mali.stage_of(path))
    except (OSError, mali.MaliocError) as e:
        return {"ok": False, "error": str(e)}


def _row(rel, info, rec, vname, v):
    row = {"file": rel, "shader": info.get("shader", ""), "pass": info.get("pass", ""),
           "keywords": " ".join(info.get("keywords", [])), "stage": rec["stage"], "variant": vname,
           "core": rec["core"], "arch": rec["arch"], "api": rec["api"], "driver": rec["driver"],
           "malioc": rec["malioc"], "bound": "+".join(v.get("bound") or []),
           "uniform_computation": rec["uniform_computation"], "source_sha1": rec["source_sha1"]}
    for p in PATHS:
        for pipe in PIPES:
            c = v.get(p)
            row[f"{p}_{pipe}"] = "" if c is None or c.get(pipe) is None else round(c[pipe], 5)
    for k in ("work_regs", "uniform_regs", "occupancy", "spilling", "spill_bytes", "fp16_pct"):
        row[k] = "" if v.get(k) is None else v[k]
    return row


def csv_rows(rel, info, rec):
    vs = rec["variants"]
    for vname, v in vs.items():
        yield _row(rel, info, rec, vname, v)
    if "position" in vs and "varying" in vs:
        p, q = vs["position"], vs["varying"]
        # both variants run per vertex (Varying only for visible ones): their sum, registers of the larger
        yield _row(rel, info, rec, IDVS_SUM, {
            **{k: add_cycles(p[k], q[k]) for k in PATHS},
            "work_regs": max(p["work_regs"] or 0, q["work_regs"] or 0),
            "spilling": bool(p["spilling"] or q["spilling"])})


def run(folder, cores=("Mali-G78",), api="gles", out=None, jobs=None):
    """Measure the folder on every core; returns (records, failures). Writes the CSV and JSONL into out."""
    if isinstance(cores, str):
        cores = [cores]
    out = out or folder
    shaders = find_shaders(folder)
    tasks = [(s, core) for s in shaders for core in cores]
    progress.phase("measure", len(tasks))
    tick = progress.counter(len(tasks))

    def job(t):
        r = measure_one(t[0][0], t[1], api)
        tick()
        return r

    with cf.ThreadPoolExecutor(jobs or os.cpu_count()) as ex:
        results = list(ex.map(job, tasks))

    records, failures = [], []
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "measurements.jsonl"), "w", encoding="utf-8", newline="\n") as fj, \
            open(os.path.join(out, "measurements.csv"), "w", encoding="utf-8", newline="") as fc:
        w = csv.DictWriter(fc, fieldnames=COLUMNS)
        w.writeheader()
        for ((path, rel, info), core), r in zip(tasks, results):
            if not r["ok"]:
                failures.append((f"{rel} [{core}]", r["error"]))
                fj.write(json.dumps({"file": rel, "ok": False, "core": core, "api": api_of(path, api),
                                     "error": r["error"]}, ensure_ascii=False) + "\n")
                continue
            rec = {"file": rel, "root": os.path.abspath(folder),
                   **{k: info[k] for k in ("shader", "pass", "keywords") if k in info},
                   **{k: v for k, v in r.items() if k != "cached"}}
            records.append((rec, r.get("cached", False)))
            fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
            w.writerows(csv_rows(rel, info, r))
    return records, failures
