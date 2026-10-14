import csv
import json
import os

CSV_COLS = ["rank", "event", "stage", "object", "shader", "pass", "keywords", "pixels", "pixel_method",
            "vertices", "vertex_method", "threads", "px_price", "vtx_price", "cs_price",
            "fragment", "vertex", "compute", "total", "share_pct", "px_bound", "vtx_bound", "px_path", "loop_n",
            "flags", "rt", "variant", "reason"]


def _loop_n(r):
    """Trip counts priced: one per outer loop ("1/0/3"), else the shader's n."""
    loop = r.get("loop") or {}
    per = [x.get("n") for x in loop.get("loops") or []]
    return "/".join(f"{round(x, 2):g}" for x in per) if per and None not in per else loop.get("n")


def write_frame_cost(cost, out_dir):
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
                    " ".join(r["keywords"]), r["pixels"], r["pixel_method"],
                    r["vertices"], r["vertex_method"], r["threads"], c.get("px_price"), c.get("vtx_price"),
                    c.get("cs_price"), round(c["fragment"]) if c else None, round(c["vertex"]) if c else None,
                    round(c["compute"]) if c else None,
                    round(c["total"]) if c else None, round(100 * c["share"], 3) if c else None,
                    "+".join(c.get("px_bound") or c.get("cs_bound") or []), "+".join(c.get("vtx_bound") or []),
                    c.get("px_path") or c.get("cs_path"), _loop_n(r),
                    " ".join(c.get("flags") or []), r["rt"], r["variant"], r["reason"]]])
