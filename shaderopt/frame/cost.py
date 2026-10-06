"""Frame cost (plan item K1.5, decision D-17: Mali-G78 is the main core, the others are switchable).

On every core: draw = pixels x fragment price + vertices x vertex price, dispatch = threads x compute price,
where a price is the bottleneck cycles of the variant on that core (profile/score.py, decision D-05: the busiest
pipe on the longest path, total when the longest path is N/A; vertex = Position + Varying, both for every
vertex — IDVS shades Varying only for visible vertices, so the vertex part is an upper bound). Pixels come from
frame/pixels.py. Vertices: RenderDoc VSInvocations when the snapshot has them (real vertex shader runs,
after the post-transform cache), else the Frame Debugger
vertex count. Threads: RenderDoc CSInvocations, else thread groups x group size from the Frame Debugger. The frame total is the sum over the measured events; every event gets its share
and the events are grouped by stage, shader, variant, object and render target.

Units: malioc cycles of that core x invocations. Cores of one generation compare; across generations the
pipe widths differ, compare shares and ranks, not the numbers.
"""
import csv
import json
import os
import re
from collections import OrderedDict, defaultdict

from shaderopt.frame import pixels as px
from shaderopt.frame import variants
from shaderopt.profile import score as heavy

GROUPS = ("stage", "shader", "variant", "object", "rt")


def label(ev):
    """Object of an event: the GameObject, the meshes of a batch, or the last marker of the path."""
    if ev.get("object"):
        return ev["object"]
    meshes = list(dict.fromkeys(m for m in ev.get("meshes") or [] if m)) or ([ev["mesh"]] if ev.get("mesh") else [])
    if meshes:
        return ", ".join(meshes[:3]) + (f" +{len(meshes) - 3}" if len(meshes) > 3 else "")
    return re.sub(r"^\(RP \d+:\d+\) ", "", (ev["path"] or "").split("/")[-1]) or ev["type"]


def vertices(ev):
    """(vertex shader runs, method) of a draw."""
    rd = ev.get("rd") or {}
    if rd.get("vs_invocations") is not None:
        return rd["vs_invocations"], "renderdoc"
    return ev["vertices"], "mesh"


def threads(ev):
    """(compute threads, method) of a dispatch."""
    rd = ev.get("rd") or {}
    if rd.get("cs_invocations") is not None:
        return rd["cs_invocations"], "renderdoc"
    c = ev.get("compute") or {}
    n = 1
    for v in (c.get("groups") or []) + (c.get("group_size") or []):
        n *= v or 1
    return (n, "dispatch") if c.get("groups") else (0, "none")


def price(recs, fp16_threshold=heavy.FP16_THRESHOLD):
    """{core: score} of one stage's records."""
    return {c: heavy.score(r, fp16_threshold) for c, r in recs.items()}


def compute(meta, events, state, frustum=None, api="vulkan", cores=None, main_core="Mali-G78"):
    """Cost of every event on every core. Returns the frame_cost.json structure."""
    pix = px.resolve(events, frustum)
    rows, missing = [], []
    found_cores = OrderedDict()
    for ev in events:
        if ev["kind"] not in ("draw", "compute"):
            continue
        recs, why = variants.match(ev, state, api)
        p = pix.get(ev["index"], {"pixels": 0, "method": None, "low": None, "high": None, "note": None})
        nv, vmethod = vertices(ev) if ev["kind"] == "draw" else (0, None)
        nt, tmethod = threads(ev) if ev["kind"] == "compute" else (0, None)
        row = {
            "index": ev["index"], "kind": ev["kind"], "stage": ev["stage"], "object": label(ev),
            "path": ev["path"], "meshes": ev.get("meshes") or [],
            "shader": ev["shader"] or (ev.get("compute") or {}).get("shader"), "pass": ev["pass"],
            "keywords": ev["keywords"],
            # a dispatch keeps the render target of the previous draw in the Frame Debugger data
            "rt": ev["rt"]["name"] if ev["kind"] == "draw" else None,
            "rt_size": [ev["rt"]["width"], ev["rt"]["height"]] if ev["kind"] == "draw" else [None, None],
            "pixels": p["pixels"], "pixel_method": p["method"], "pixels_low": p["low"], "pixels_high": p["high"],
            "pixel_note": p["note"], "vertices": nv, "vertex_method": vmethod, "threads": nt, "thread_method": tmethod,
            "kernel": (ev.get("compute") or {}).get("kernel"),
            "variant": variants.key_str(variants.key_of(ev)),
            "files": {}, "cost": {}, "reason": why,
        }
        if recs and "compute" in recs:
            for c, sc in price(recs["compute"]).items():
                found_cores[c] = recs["compute"][c]["arch"]
                cc = nt * sc["cycles"]
                row["files"] = {"compute": next(iter(recs["compute"].values()))["file"]}
                row["cost"][c] = {
                    "px_price": None, "vtx_price": None, "cs_price": sc["cycles"], "cs_bound": sc["bound"],
                    "cs_path": sc["path"], "px_bound": [], "vtx_bound": [], "px_path": None, "vtx_path": None,
                    "fragment": 0.0, "vertex": 0.0, "compute": cc, "total": cc, "flags": sc["flags"],
                    "px_regs": sc["work_regs"], "vtx_regs": None, "px_fp16": sc["fp16_pct"],
                }
        elif recs and len(recs) == 2:
            frag, vert = price(recs["fragment"]), price(recs["vertex"])
            row["files"] = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
            for c in frag:
                if c not in vert:
                    continue
                found_cores[c] = next(r["arch"] for r in recs["fragment"].values() if r["core"] == c)
                f, v = frag[c], vert[c]
                cf, cv = row["pixels"] * f["cycles"], row["vertices"] * v["cycles"]
                row["cost"][c] = {
                    "px_price": f["cycles"], "vtx_price": v["cycles"],
                    "px_bound": f["bound"], "vtx_bound": v["bound"], "px_path": f["path"], "vtx_path": v["path"],
                    "fragment": cf, "vertex": cv, "compute": 0.0, "total": cf + cv,
                    "flags": sorted(set(f["flags"]) | {x for x in v["flags"] if x != "low_fp16"}),
                    "px_regs": f["work_regs"], "vtx_regs": v["work_regs"], "px_fp16": f["fp16_pct"],
                }
        else:
            missing.append({"index": ev["index"], "kind": ev["kind"], "stage": ev["stage"], "object": row["object"],
                            "shader": row["shader"], "pass": row["pass"], "reason": why or "not measured"})
        rows.append(row)

    cores = [c for c in (cores or found_cores) if c in found_cores]
    if main_core not in cores and cores:
        main_core = cores[0]
    totals = {}
    for c in cores:
        t = sum(r["cost"][c]["total"] for r in rows if c in r["cost"])
        totals[c] = {"total": t, **{k: sum(r["cost"][c][k] for r in rows if c in r["cost"])
                                    for k in ("fragment", "vertex", "compute")}}
        for r in rows:
            if c in r["cost"]:
                r["cost"][c]["share"] = r["cost"][c]["total"] / t if t else 0.0
    rows.sort(key=lambda r: -(r["cost"].get(main_core, {}).get("total", -1)))
    draws = [r for r in rows if r["kind"] == "draw"]
    return {
        "frame": {k: v for k, v in meta.items() if k != "events"},
        "api": api, "cores": [{"name": c, "arch": found_cores[c]} for c in cores], "main_core": main_core,
        "totals": totals,
        "coverage": {"draws": len(draws), "draws_priced": sum(1 for r in draws if r["cost"]),
                     "events": len(rows), "events_priced": sum(1 for r in rows if r["cost"])},
        "pixel_methods": dict(sorted(_count(r["pixel_method"] for r in draws).items())),
        "vertex_methods": dict(sorted(_count(r["vertex_method"] for r in draws).items())),
        "groups": {c: groups(rows, c) for c in cores},
        "missing": missing, "events": rows,
    }


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x or "none"] += 1
    return d


def groups(rows, core):
    """{group: [{key, events, pixels, vertices, threads, fragment, vertex, compute, total, share}]} by total."""
    out = {}
    for g in GROUPS:
        acc = OrderedDict()
        for r in rows:
            c = r["cost"].get(core)
            if not c:
                continue
            k = {"stage": r["stage"], "shader": r["shader"], "rt": r["rt"], "object": r["object"],
                 "variant": r["variant"]}[g] or "—"
            a = acc.setdefault(k, {"key": k, "events": 0, "pixels": 0, "vertices": 0, "threads": 0,
                                   "fragment": 0.0, "vertex": 0.0, "compute": 0.0, "total": 0.0, "share": 0.0})
            a["events"] += 1
            for f in ("pixels", "vertices", "threads"):
                a[f] += r[f]
            for f in ("fragment", "vertex", "compute", "total", "share"):
                a[f] += c[f]
        out[g] = sorted(acc.values(), key=lambda a: -a["total"])
    return out


CSV_COLS = ["rank", "index", "stage", "object", "shader", "pass", "keywords", "pixels", "pixel_method",
            "pixels_low", "pixels_high", "vertices", "vertex_method", "threads", "px_price", "vtx_price", "cs_price",
            "fragment", "vertex", "compute", "total", "share_pct", "px_bound", "vtx_bound", "flags", "rt", "variant",
            "reason"]


def write(cost, out_dir):
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
                    core, i if c else "", r["index"], r["stage"], r["object"], r["shader"], r["pass"],
                    " ".join(r["keywords"]), r["pixels"], r["pixel_method"], r["pixels_low"], r["pixels_high"],
                    r["vertices"], r["vertex_method"], r["threads"], c.get("px_price"), c.get("vtx_price"),
                    c.get("cs_price"), round(c["fragment"]) if c else None, round(c["vertex"]) if c else None,
                    round(c["compute"]) if c else None,
                    round(c["total"]) if c else None, round(100 * c["share"], 3) if c else None,
                    "+".join(c.get("px_bound") or c.get("cs_bound") or []), "+".join(c.get("vtx_bound") or []),
                    " ".join(c.get("flags") or []), r["rt"], r["variant"], r["reason"]]])
