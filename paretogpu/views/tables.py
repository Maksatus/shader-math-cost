"""CSV and JSON files of the results: measurements.csv, report.csv, frame_cost.json / frame_cost.csv."""
import csv
import json
import os

from paretogpu.core.pricing import add_cycles
from paretogpu.model.cores import ARCH_SHORT, ARCHS
from paretogpu.model.measurement import PIPES

# --- measurements.csv (app/measure.py) ---------------------------------------------------------------------------
PATHS = ("longest", "shortest", "total")
COLUMNS = (["file", "shader", "pass", "keywords", "stage", "variant",
            "core", "arch", "api", "driver", "malioc", "bound"]
           + [f"{p}_{pipe}" for p in PATHS for pipe in PIPES]
           + ["work_regs", "uniform_regs", "occupancy", "spilling", "spill_bytes",
              "fp16_pct", "uniform_computation", "source_sha1"])
IDVS_SUM = "position+varying"


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


# --- report.csv (core/ranking.py) --------------------------------------------------------------------------------
def write_report_csv(rows, cores, path):
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


# --- frame_cost.json / frame_cost.csv (core/frame_cost.py) --------------------------------------------------------
CSV_COLS = ["rank", "event", "stage", "object", "shader", "pass", "keywords", "pixels", "pixel_method",
            "pixels_low", "pixels_high", "vertices", "vertex_method", "threads", "px_price", "vtx_price", "cs_price",
            "fragment", "vertex", "compute", "total", "share_pct", "px_bound", "vtx_bound", "px_path", "loop_n",
            "flags", "rt", "variant", "reason"]


def write_frame_cost(cost, out_dir):
    """frame_cost.json (all cores) and frame_cost.csv (one row per event x core)."""
    with open(os.path.join(out_dir, "frame_cost.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(cost, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "frame_cost.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["core"] + CSV_COLS)
        for core in [c["name"] for c in cost["cores"]] or [None]:
            ranked = sorted(cost["events"], key=lambda r: -(r["cost"].get(core, {}).get("total", -1)))
            for i, r in enumerate(ranked, 1):
                c = r["cost"].get(core) or {}
                w.writerow(["" if v is None else v for v in [
                    core, i if c else "", r["index"] + 1, r["stage"], r["object"], r["shader"], r["pass"],
                    " ".join(r["keywords"]), r["pixels"], r["pixel_method"], r["pixels_low"], r["pixels_high"],
                    r["vertices"], r["vertex_method"], r["threads"], c.get("px_price"), c.get("vtx_price"),
                    c.get("cs_price"), round(c["fragment"]) if c else None, round(c["vertex"]) if c else None,
                    round(c["compute"]) if c else None,
                    round(c["total"]) if c else None, round(100 * c["share"], 3) if c else None,
                    "+".join(c.get("px_bound") or c.get("cs_bound") or []), "+".join(c.get("vtx_bound") or []),
                    c.get("px_path") or c.get("cs_path"), (r.get("loop") or {}).get("n"),
                    " ".join(c.get("flags") or []), r["rt"], r["variant"], r["reason"]]])
