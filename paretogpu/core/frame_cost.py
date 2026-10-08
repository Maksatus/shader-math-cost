"""Frame cost (plan item K1.5, decision D-17: Mali-G78 is the main core, the others are switchable).

On every core: draw = pixels x fragment price + vertices x vertex price, dispatch = threads x compute price,
where a price is the bottleneck cycles of the variant on that core (core/pricing.py, decision D-05: the busiest
pipe on the longest path; a shader whose longest path is N/A on some core (dynamic loops: lights, probes, ray
marching) is priced with its loops forced to n iterations (core/loops.py; n = --loop-iters, default 2, and the
frame is also summed at n = 0, 1, 2, 4, 8 for the range in the report); vertex = Position + Varying, both for every
vertex — IDVS shades Varying only for visible vertices, so the vertex part is an upper bound). Pixels come from
core/pixels.py. Vertices: RenderDoc VSInvocations when the snapshot has them (real vertex shader runs,
after the post-transform cache), else the Frame Debugger
vertex count. Threads: RenderDoc CSInvocations, else thread groups x group size from the Frame Debugger. The frame total is the sum over the measured events; every event gets its share
and the events are grouped by stage, shader, variant, object and render target.

Units: malioc cycles of that core x invocations. Cores of one generation compare; across generations the
pipe widths differ, compare shares and ranks, not the numbers.
"""
import re
from collections import OrderedDict, defaultdict

from paretogpu.core import pixels as px
from paretogpu.core import variants
from paretogpu.core.pricing import LOOP_ITERS, LOOP_NS, loop_n, price
from paretogpu.model.cores import MAIN_CORE
from paretogpu.model.cost import CostRun
from paretogpu.model.frame import Event
from paretogpu.model.variant import VariantKey, VariantState

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


def compute(meta: dict, events: list[Event], state: VariantState, api="vulkan", cores=None, main_core=MAIN_CORE,
            loop_iters=LOOP_ITERS, loop_overrides=None, loop_ns=LOOP_NS) -> CostRun:
    """Cost of every event on every core. Returns the frame_cost.json structure.
    loop_iters: trip count of every outer dynamic loop (lights, probes, ray steps) of a shader whose longest path
    is N/A on some core (state["loops"], core/loops.py); loop_overrides: {part of a shader name: n};
    loop_ns: the trip counts of the range shown in the report."""
    pix = px.resolve(events)
    loop_data = state.get("loops") or {}
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
            "variant": str(VariantKey.of(ev)),
            "files": {}, "cost": {}, "reason": why, "loop": None,
        }
        n_ev, fixed = loop_n(row["shader"], loop_iters, loop_overrides)
        if recs and "compute" in recs:
            fn = next(iter(recs["compute"].values()))["file"]
            row["files"] = {"compute": fn}
            for c in recs["compute"]:
                sc = price(recs["compute"], fn, c, n_ev, loop_data)
                found_cores[c] = recs["compute"][c]["arch"]
                cc = nt * sc["cycles"]
                row["cost"][c] = {
                    "px_price": None, "vtx_price": None, "cs_price": sc["cycles"], "cs_bound": sc["bound"],
                    "cs_path": sc["path"], "px_bound": [], "vtx_bound": [], "px_path": None, "vtx_path": None,
                    "fragment": 0.0, "vertex": 0.0, "compute": cc, "total": cc, "flags": sc["flags"],
                    "px_regs": sc["work_regs"], "vtx_regs": None, "px_fp16": sc["fp16_pct"],
                }
                if (loop_data.get(fn) or {}).get(c) and not fixed:
                    row["cost"][c]["total_by_n"] = {
                        str(n): nt * price(recs["compute"], fn, c, n, loop_data)["cycles"] for n in loop_ns}
        elif recs and len(recs) == 2:
            row["files"] = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
            ff, fv = row["files"]["fragment"], row["files"]["vertex"]
            for c in recs["fragment"]:
                if c not in recs["vertex"]:
                    continue
                f = price(recs["fragment"], ff, c, n_ev, loop_data)
                v = price(recs["vertex"], fv, c, n_ev, loop_data)
                found_cores[c] = recs["fragment"][c]["arch"]
                cf, cv = row["pixels"] * f["cycles"], row["vertices"] * v["cycles"]
                row["cost"][c] = {
                    "px_price": f["cycles"], "vtx_price": v["cycles"],
                    "px_bound": f["bound"], "vtx_bound": v["bound"], "px_path": f["path"], "vtx_path": v["path"],
                    "fragment": cf, "vertex": cv, "compute": 0.0, "total": cf + cv,
                    "flags": sorted(set(f["flags"]) | {x for x in v["flags"] if x != "low_fp16"}),
                    "px_regs": f["work_regs"], "vtx_regs": v["work_regs"], "px_fp16": f["fp16_pct"],
                }
                looped = (loop_data.get(ff) or {}).get(c) or (loop_data.get(fv) or {}).get(c)
                if looped and not fixed:
                    by_n, px_by_n = {}, {}
                    for n in loop_ns:
                        fp, vp = price(recs["fragment"], ff, c, n, loop_data), price(recs["vertex"], fv, c, n, loop_data)
                        by_n[str(n)] = row["pixels"] * fp["cycles"] + row["vertices"] * vp["cycles"]
                        px_by_n[str(n)] = fp["cycles"]
                    row["cost"][c]["total_by_n"], row["cost"][c]["px_price_by_n"] = by_n, px_by_n
        else:
            missing.append({"index": ev["index"], "kind": ev["kind"], "stage": ev["stage"], "object": row["object"],
                            "shader": row["shader"], "pass": row["pass"], "reason": why or "not measured"})
        if any(str(x.get(k) or "").startswith("loop") for x in row["cost"].values()
               for k in ("px_path", "vtx_path", "cs_path")):
            row["loop"] = {"n": n_ev, "fixed": fixed}
        rows.append(row)

    cores = [c for c in (cores or found_cores) if c in found_cores]
    if main_core not in cores and cores:
        main_core = cores[0]
    totals, by_n = {}, {}
    for c in cores:
        t = sum(r["cost"][c]["total"] for r in rows if c in r["cost"])
        totals[c] = {"total": t, **{k: sum(r["cost"][c][k] for r in rows if c in r["cost"])
                                    for k in ("fragment", "vertex", "compute")}}
        for r in rows:
            if c in r["cost"]:
                r["cost"][c]["share"] = r["cost"][c]["total"] / t if t else 0.0
        # the frame and its stages at every trip count of the range (events with fixed or no loops stay)
        by_n[c] = {}
        for n in loop_ns:
            stage, looped = defaultdict(float), 0.0
            for r in rows:
                x = r["cost"].get(c)
                if not x:
                    continue
                v = (x.get("total_by_n") or {}).get(str(n), x["total"])
                stage[r["stage"]] += v
                looped += v if r["loop"] else 0.0
            tot = sum(stage.values())
            by_n[c][str(n)] = {"total": tot, "loop_share": looped / tot if tot else 0.0,
                               "stages": {k: v / tot if tot else 0.0
                                          for k, v in sorted(stage.items(), key=lambda kv: -kv[1])}}
    rows.sort(key=lambda r: -(r["cost"].get(main_core, {}).get("total", -1)))
    draws = [r for r in rows if r["kind"] == "draw"]
    return {
        "frame": {k: v for k, v in meta.items() if k != "events"},
        "api": api, "cores": [{"name": c, "arch": found_cores[c]} for c in cores], "main_core": main_core,
        "totals": totals,
        "loops": {"n": loop_iters, "overrides": dict(loop_overrides or {}), "range": list(loop_ns),
                  "events": sum(1 for r in rows if r["loop"]), "files": len(loop_data),
                  "unforced": sorted(f for f, d in loop_data.items() if len(d) < len(cores)), "by_n": by_n},
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
