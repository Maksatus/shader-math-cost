"""Frame cost per core: draw = pixels x px_price + vertices x vtx_price, dispatch = threads x cs_price (core/pricing).
Units: malioc cycles x invocations; only cores of one generation compare in absolute numbers (pipe widths differ).
Vertex price = Position + Varying for every vertex: IDVS shades Varying only for visible ones, so it is an upper bound;
a draw without a fragment shader (or with no fragment threads) runs Position only.
px_ref / vtx_ref / cs_ref = price with every outer loop once (REF_N): trip-count independent, used by core/compare.
"""
import math
import os
import re
from collections import OrderedDict, defaultdict

from paretogpu.core import loops
from paretogpu.core.memory import TILE_PRICE
from paretogpu.core import pixels as px
from paretogpu.core import profile
from paretogpu.core import variants
from paretogpu.core.pricing import LOOP_ITERS, combined, loop_parts, price, price_draw
from paretogpu.model.cores import MAIN_CORE
from paretogpu.model.cost import CostRun, CostSummary
from paretogpu.model.frame import Event
from paretogpu.model.variant import VariantKey, VariantState

REF_N = 1


def label(ev):
    if ev.get("object"):
        return ev["object"]
    meshes = list(dict.fromkeys(m for m in ev.get("meshes") or [] if m)) or ([ev["mesh"]] if ev.get("mesh") else [])
    if meshes:
        return ", ".join(meshes[:3]) + (f" +{len(meshes) - 3}" if len(meshes) > 3 else "")
    return re.sub(r"^\(RP \d+:\d+\) ", "", (ev["path"] or "").split("/")[-1]) or ev["type"]


def vertices(ev):
    rd =ev.get("rd") or {}
    if rd.get("vs_invocations") is not None:
        return rd["vs_invocations"], rd.get("vertex_method") or "renderdoc"
    return ev["vertices"], "mesh"


def threads(ev):
    rd =ev.get("rd") or {}
    if rd.get("cs_invocations") is not None:
        return rd["cs_invocations"], "renderdoc"
    c = ev.get("compute") or {}
    groups = c.get("groups") or []
    if not groups or any(v is None for v in groups):
        return 0, "none"
    n = 1
    for v in groups + [v or 1 for v in c.get("group_size") or []]:
        n *= v
    return n, "dispatch"


def _event_row(ev, pix, why):
    p =pix.get(ev["index"], {"pixels": 0, "method": None, "note": None})
    nv, vmethod = vertices(ev) if ev["kind"] == "draw" else (0, None)
    nt, tmethod = threads(ev) if ev["kind"] == "compute" else (0, None)
    return {
        "index": ev["index"], "kind": ev["kind"], "stage": ev["stage"], "object": label(ev),
        "path": ev["path"], "meshes": ev.get("meshes") or [],
        "shader": ev["shader"] or (ev.get("compute") or {}).get("shader"), "pass": ev["pass"],
        "keywords": ev["keywords"],
        # Frame Debugger data of a dispatch keeps the previous draw's render target
        "rt": ev["rt"]["name"] if ev["kind"] == "draw" else None,
        "rt_size": [ev["rt"]["width"], ev["rt"]["height"]] if ev["kind"] == "draw" else [None, None],
        "pixels": p["pixels"], "pixel_method": p["method"], "pixel_note": p["note"], "vertices": nv, "vertex_method": vmethod, "threads": nt, "thread_method": tmethod,
        "kernel": (ev.get("compute") or {}).get("kernel"),
        "variant": str(VariantKey.of(ev)), "key": VariantKey.of(ev).to_list(),
        "files": {}, "cost": {}, "reason": why, "loop": None,
    }


KNOWN = ("uniform", "frame")
FRAG = (".frag", ".frag.spv")


class _Counts:
    """Outer loop trip counts: uniform bound from params, else measured mean per pixel (frag only), else LOOP_ITERS.
    calls {event: rd_actions action}: an SRP batch's calls draw objects of other uniform values (block bytes per call),
    their counts are averaged by the call's invocations of the stage."""

    def __init__(self, ev, loop_data, bounds, iters=None, calls=None):
        self.ev, self.loop_data, self.bounds, self.n = ev, loop_data, bounds, LOOP_ITERS
        self.iters = iters
        self.calls = [calls[e] for e in (ev.get("rd") or {}).get("events") or [] if e in (calls or {})]

    def _uniform(self, b, fn):
        """-> (trip count, bound value) of bound b, None if unreadable."""
        params = self.ev.get("params") or {}
        counter = ("ps_invocations" if fn.endswith(FRAG) else "cs_invocations" if self.ev["kind"] == "compute"
                   else "vs_invocations")
        calls = [({**params, **(a.get("blocks") or {})}, a.get(counter) or 0) for a in self.calls] or [(params, 0)]
        vals = [(loops.bound_value(b, p), w) for p, w in calls]
        if any(v is None for v, _ in vals):
            return None
        total = sum(w for _, w in vals)
        share = [w / total if total else 1 / len(vals) for _, w in vals]
        return (sum(max(0, math.ceil(v - 1e-6)) * k for (v, _), k in zip(vals, share)),
                sum(v * k for (v, _), k in zip(vals, share)))

    def sources(self, fn):
        p = next(iter((self.loop_data.get(fn) or {}).values()), None)
        if not p:
            return None
        size = len(p["loops"])
        measured = self.iters if self.iters and len(self.iters["n"]) == size and fn.endswith(FRAG) else None
        out = []
        for k, b in enumerate(self.bounds.get(fn) or [None] * size):
            u = self._uniform(b, fn)
            if u is not None:
                out.append({"n": u[0], "from": "uniform", "bound": loops.bound_text(b), "value": u[1]})
            elif measured:
                out.append({"n": measured["n"][k], "from": "frame", "pixels": measured["pixels"]})
            else:
                out.append({"n": self.n, "from": "default"})
        return out

    def at(self, fn):
        src = self.sources(fn)
        return self.n if src is None else [x["n"] for x in src]


def _price_compute(row, recs, counts, loop_data):
    fn =next(iter(recs["compute"].values()))["file"]
    row["files"] = {"compute": fn}
    found = {}
    n = counts.at(fn)
    for c in recs["compute"]:
        sc = price(recs["compute"], fn, c, n, loop_data)
        found[c] = recs["compute"][c]["arch"]
        cc = row["threads"] * sc["cycles"]
        row["cost"][c] = {
            "px_price": None, "vtx_price": None, "cs_price": sc["cycles"], "cs_bound": sc["bound"],
            "cs_path": sc["path"], "px_bound": [], "vtx_bound": [], "px_path": None, "vtx_path": None,
            "fragment": 0.0, "vertex": 0.0, "compute": cc, "total": cc, "flags": sc["flags"],
            "px_regs": sc["work_regs"], "vtx_regs": None, "px_fp16": sc["fp16_pct"], "cs_ref": sc["cycles"],
        }
        if (loop_data.get(fn) or {}).get(c):
            row["cost"][c]["cs_loops"] = loop_parts(recs["compute"], fn, c, n, loop_data)
            row["cost"][c]["cs_ref"] = price(recs["compute"], fn, c, REF_N, loop_data)["cycles"]
    return found


GEOMETRY = ("shadow", "prepass")  # passes bound by the tiler on Mali (K3.1: model 0.23..0.34 of the counters): not priced
NO_SHADER = {"cycles": 0.0, "bound": [], "path": None, "flags": [], "work_regs": None, "fp16_pct": None}


def _position_only(recs, core):
    """Without a fragment shader IDVS runs no varying shading (K3.1: 0 varying threads in the shadow passes)."""
    rec = recs[core]
    if "position" not in rec.get("variants", {}):
        return recs
    return {**recs, core: {**rec, "variants": {"position": rec["variants"]["position"]}}}


def _price_draw(row, recs, counts, loop_data, shaded):
    """recs with "vertex" alone (device depth-only draw): pixel price 0. shaded: the draw ran fragment threads at all
    (before Forward Pixel Kill, which leaves the varying shading in place)."""
    row["files"] = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
    ff, fv = row["files"].get("fragment"), row["files"]["vertex"]
    n = {"fragment": counts.at(ff) if ff else 0, "vertex": counts.at(fv)}
    found = {}
    for c in recs.get("fragment") or recs["vertex"]:
        if c not in recs["vertex"]:
            continue
        if ff:
            d = price_draw(recs, row["files"], c, n, loop_data)
            if not shaded:  # a PC frame's depth-only draw keeps the variant's empty fragment shader
                d["vtx"] = price(_position_only(recs["vertex"], c), fv, c, n["vertex"], loop_data)
        else:
            v = price(_position_only(recs["vertex"], c), fv, c, n["vertex"], loop_data)
            d = {"px": NO_SHADER, "vtx": v, "flags": [x for x in v["flags"] if x != "low_fp16"]}
        f, v = d["px"], d["vtx"]
        found[c] = recs["vertex"][c]["arch"]
        cf, cv = row["pixels"] * f["cycles"], row["vertices"] * v["cycles"]
        row["cost"][c] = {
            "px_price": f["cycles"], "vtx_price": v["cycles"],
            "px_bound": f["bound"], "vtx_bound": v["bound"], "px_path": f["path"], "vtx_path": v["path"],
            "fragment": cf, "vertex": cv, "compute": 0.0, "total": cf + cv, "flags": d["flags"],
            "px_regs": f["work_regs"], "vtx_regs": v["work_regs"], "px_fp16": f["fp16_pct"],
            "px_ref": f["cycles"], "vtx_ref": v["cycles"],
        }
        if (loop_data.get(ff) or {}).get(c):
            row["cost"][c]["px_loops"] = loop_parts(recs["fragment"], ff, c, n["fragment"], loop_data)
            row["cost"][c]["px_ref"] = price(recs["fragment"], ff, c, REF_N, loop_data)["cycles"]
        if (loop_data.get(fv) or {}).get(c):
            row["cost"][c]["vtx_ref"] = price(recs["vertex"], fv, c, REF_N, loop_data)["cycles"]
    return found


TILE = "Mali tile threads"


def _tile_row(index, t, cores):
    """Mali's own fragment threads of a render pass (tile preload / resolve), core/memory.TILE_PRICE each."""
    what = " + ".join(dict.fromkeys(p[0] for p in t["parts"]))
    return {
        "index": index, "kind": "tile", "stage": "tile", "object": f"{TILE}: {what}", "path": t["path"], "meshes": [],
        "shader": TILE, "pass": what, "keywords": [], "rt": t["parts"][0][1], "rt_size": [None, None],
        "pixels": t["threads"], "pixel_method": "tile", "pixel_note": None, "vertices": 0, "vertex_method": None,
        "threads": 0, "thread_method": None, "kernel": None, "variant": f"{TILE} | {what}", "key": [TILE, what],
        "files": {}, "reason": None, "loop": None, "tile": t["parts"], "tile_event": t["event"],
        "cost": {c: {"px_price": TILE_PRICE, "vtx_price": None, "px_bound": [], "vtx_bound": [], "px_path": None,
                     "vtx_path": None, "fragment": t["threads"] * TILE_PRICE, "vertex": 0.0, "compute": 0.0,
                     "total": t["threads"] * TILE_PRICE, "flags": [], "px_regs": None, "vtx_regs": None,
                     "px_fp16": None, "px_ref": TILE_PRICE, "vtx_ref": None} for c in cores},
    }


def _totals(rows, cores):
    totals = {}
    for c in cores:
        t = sum(r["cost"][c]["total"] for r in rows if c in r["cost"])
        totals[c] = {"total": t, **{k: sum(r["cost"][c][k] for r in rows if c in r["cost"])
                                    for k in ("fragment", "vertex", "compute")}}
        for r in rows:
            if c in r["cost"]:
                r["cost"][c]["share"] = r["cost"][c]["total"] / t if t else 0.0
    return totals


def _default_loop(r):
    """A row with work whose dynamic loop runs the default trip count (LOOP_ITERS), not its parameters' or the capture's."""
    lp = r["loop"]
    return bool(lp and (r["pixels"] or r["threads"]) and (not lp["loops"] or any(x["from"] not in KNOWN
                                                                                    for x in lp["loops"])))


def _unforced(loop_data, measurements, cores):
    """Files without loop data on a core where malioc's longest path is N/A (priced by total cycles there)."""
    def na(fn, c):
        r = (measurements.get(fn) or {}).get(c)
        return r is not None and r.get("ok", True) and combined(r)["longest"] is None
    return sorted(fn for fn, d in loop_data.items() if any(na(fn, c) and c not in d for c in cores))


def compute(meta: dict, events: list[Event], state: VariantState, cores=None,
            main_core=MAIN_CORE, tiles=None, calls=None) -> CostRun:
    """-> frame_cost.json. An event priced on only some cores is in "missing" with "cores" = the unpriced ones.
    tiles: core/memory.tile_threads(), priced as rows of kind "tile"; calls: {event: rd_actions action} (_Counts)."""
    pix = px.resolve(events)
    loop_data = state.get("loops") or {}
    bounds = state.get("loop_bounds") or {}
    iters = state.get("loop_iters") or {}
    rows, missing = [], []
    found_cores = OrderedDict()
    for ev in events:
        if ev["kind"] not in ("draw", "compute"):
            continue
        recs, why = variants.match(ev, state)
        row = _event_row(ev, pix, why)
        counts = _Counts(ev, loop_data, bounds, iters.get(ev["index"]), calls)
        if recs and "compute" in recs:
            found_cores.update(_price_compute(row, recs, counts, loop_data))
        elif recs and (len(recs) == 2 or (set(recs) == {"vertex"} and not why)):
            ps = (ev.get("rd") or {}).get("ps_invocations")
            found_cores.update(_price_draw(row, recs, counts, loop_data, bool(row["pixels"] if ps is None else ps)))
        else:
            missing.append({"index": ev["index"], "kind": ev["kind"], "stage": ev["stage"], "object": row["object"],
                            "shader": row["shader"], "pass": row["pass"], "reason": why or "not measured"})
        if any(str(x.get(k) or "").startswith("loop") for x in row["cost"].values()
               for k in ("px_path", "vtx_path", "cs_path")):
            main = row["files"].get("fragment") or row["files"].get("compute")
            row["loop"] = {"n": LOOP_ITERS, "loops": counts.sources(main)}
        rows.append(row)

    cores = [c for c in (cores or found_cores) if c in found_cores]
    if main_core not in cores and cores:
        main_core = cores[0]
    listed = {m["index"] for m in missing}
    for r in rows:
        lost = [c for c in cores if c not in r["cost"]]
        if lost and r["index"] not in listed:
            missing.append({"index": r["index"], "kind": r["kind"], "stage": r["stage"], "object": r["object"],
                            "shader": r["shader"], "pass": r["pass"], "cores": lost,
                            "reason": ("no price on " + ", ".join(lost)) if r["cost"]
                            else r["reason"] or "no core has both stages measured"})
    top = max((r["index"] for r in rows), default=-1)
    rows += [_tile_row(top + 1 + k, t, cores) for k, t in enumerate(tiles or [])]
    tri = {ev["index"]: (ev.get("indices") or ev.get("vertices") or 0) // 3 for ev in events}
    geometry = [{**r, "geometry": True, "triangles": tri.get(r["index"], 0)} for r in rows if r["stage"] in GEOMETRY]
    priced = [r for r in rows if r["stage"] not in GEOMETRY]
    totals = _totals(priced, cores)
    priced.sort(key=lambda r: -(r["cost"].get(main_core, {}).get("total", -1)))
    geometry.sort(key=lambda r: -r["vertices"])
    draws = [r for r in rows if r["kind"] == "draw"]
    rows = priced
    return {
        "frame": {k: v for k, v in meta.items() if k not in ("events", "pipeline")},
        "cores": [{"name": c, "arch": found_cores[c]} for c in cores], "main_core": main_core,
        "totals": totals,
        "loops": {"n": LOOP_ITERS, "events": sum(1 for r in rows if r["loop"]), "files": len(loop_data),
                  "bounded": sum(1 for r in rows if r["loop"] and any(x["from"] == "uniform"
                                                                         for x in r["loop"]["loops"] or [])),
                  "measured": sum(1 for r in rows if r["loop"] and any(x["from"] == "frame"
                                                                          for x in r["loop"]["loops"] or [])),
                  "unknown": sum(1 for r in rows if _default_loop(r)),
                  "unforced": _unforced(loop_data, state.get("measurements") or {}, cores)},
        "coverage": {"draws": len(draws), "draws_priced": sum(1 for r in draws if r["cost"]),
                     "events": len(rows) + len(geometry) - len(tiles or []),
                     "events_priced": sum(1 for r in rows + geometry if r["cost"] and r["kind"] != "tile")},
        "pixel_methods": dict(sorted(_count(r["pixel_method"] for r in draws).items())),
        "vertex_methods": dict(sorted(_count(r["vertex_method"] for r in draws).items())),
        "missing": missing, "events": rows, "geometry": geometry,
    }


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x or "none"] += 1
    return d


def group(rows, core, by):
    """Rows priced on core summed by "stage" | "shader" | "variant" | "object" | "rt", costliest first (the report
    groups in the page, views/static/result/cost.js)."""
    acc = OrderedDict()
    for r in rows:
        c = r["cost"].get(core)
        if not c:
            continue
        k = r[by] or "—"
        a = acc.setdefault(k, {"key": k, "events": 0, "pixels": 0, "vertices": 0, "threads": 0,
                               "fragment": 0.0, "vertex": 0.0, "compute": 0.0, "total": 0.0})
        a["events"] += 1
        for f in ("pixels", "vertices", "threads"):
            a[f] += r[f]
        for f in ("fragment", "vertex", "compute", "total"):
            a[f] += c[f]
    t = sum(a["total"] for a in acc.values())
    return sorted(({**a, "share": a["total"] / t if t else 0.0} for a in acc.values()), key=lambda a: -a["total"])


def summary(c: CostRun) -> CostSummary:
    frame,prof, cov = c.get("frame") or {}, c.get("profile") or {}, c.get("coverage") or {}
    fd = c.get("frame_dir") or ""
    mc = c.get("main_core")
    t = (c.get("totals") or {}).get(mc) or {}
    missing = [m for m in c.get("missing") or [] if not m.get("cores") or len(m["cores"]) == len(c.get("cores") or [])]
    failed = [m for m in missing if m.get("kind") == "draw"]
    top = group(c.get("events") or [], mc, "shader")[:5]
    return {"snapshot": os.path.basename(fd.rstrip("\\/")) or None, "frame_dir": fd, "project": frame.get("project"),
            "computed_at": c.get("computed_at"), "code_version": c.get("code_version"),
            "malioc": c.get("malioc"), "main_core": mc,
            "cores": [x["name"] for x in c.get("cores") or []], "loops_n": (c.get("loops") or {}).get("n"),
            "checked": c.get("variants_checked"), "resolution": profile.frame_size(c.get("events")),
            "quality": frame.get("quality"), "pipeline": prof.get("pipeline"), "scale": prof.get("frame_scale"),
            "render": prof.get("render"),
            "renderdoc": bool(frame.get("renderdoc")),
            "total": t.get("total"), "fragment": t.get("fragment"), "vertex": t.get("vertex"),
            "compute": t.get("compute"), "totals": {k: v.get("total") for k, v in (c.get("totals") or {}).items()},
            "coverage": c.get("coverage"), "priced": cov.get("draws_priced"), "pixel_methods": c.get("pixel_methods"),
            "vertex_methods": c.get("vertex_methods"),
            "shaders": [{"key": g.get("key"), "share": g.get("share"), "total": g.get("total")} for g in top],
            "unpriced_draws": len(failed), "unpriced_compute": len(missing) - len(failed),
            "failed_reasons": sorted({(m.get("reason") or "")[:200] for m in failed})[:5]}


BOUND = {"A": "arith", "LS": "ls", "T": "t", "V": "v"}


def knockout_cycles(s):
    """A knocked-out shader's cycles: the sum of its unit deltas, as the model sums pipes; a unit below 0 is noise."""
    return sum(max(0.0, v or 0.0) for v in s["pipes"].values())


def measured(c, sc, events, core):
    """Phone snapshot: the cost of each event on `core` from the shader knockout (sc, shader_cost) in place of the
    model, by the model's rules (pipe sum, geometry apart, Mali tile rows as modelled). A shader's cycles go to its
    draws by pixels; a vertex shader's whole-draw cycles beyond the fragment part of those draws go to them by vertices,
    or to their pixels when a draw has more pixels than vertices. A part the knockout did not measure above 0 keeps
    the model's price, cost["unmeasured"] says which and why. -> False when nothing was measured."""
    if not sc or sc.get("error") or not any(not s.get("error") for s in sc.get("shaders") or []):
        return False
    rows = [r for r in c["events"] + c["geometry"] if r["kind"] != "tile"]
    by = {r["index"]: r for r in rows}
    calls = {e: ev["index"] for ev in events for e in (ev.get("rd") or {}).get("events") or [] if ev["index"] in by}

    def share(v, idx, key):
        w = [by[i][key] or 0 for i in idx]
        t = sum(w)
        return {i: v * (x / t if t else 1 / len(idx)) for i, x in zip(idx, w)}

    def busiest(s):
        k = max(s["pipes"], key=s["pipes"].get, default=None)
        return [BOUND.get(k, k)] if k and s["pipes"][k] > 0 else []

    def model(i, part):
        return ((by[i]["cost"].get(core) or {}).get(part)) or 0.0

    frag, vert, fb, vb, why = {}, {}, {}, {}, {}
    for mode, acc, bound in (("colour", frag, fb), ("draw", vert, vb)):
        part = "fragment" if mode == "colour" else "vertex"
        for s in sc["shaders"]:
            idx = sorted({calls[e] for e in s["events"] if e in calls})
            if s["mode"] != mode or not idx:
                continue
            if s.get("error"):
                for i in idx:
                    why.setdefault((i, part), s["error"])
                continue
            v = knockout_cycles(s)
            if mode == "draw":
                v -= sum(frag[i] if i in frag else model(i, "fragment") for i in idx)
            if v <= 0:
                for i in idx:
                    why.setdefault((i, part), "noise")
                continue
            for i, x in share(v, idx, "pixels" if mode == "colour" else "vertices").items():
                acc[i] = acc.get(i, 0.0) + x
                bound[i] = busiest(s)
    priced, missing, fallback = [], [], 0.0
    for r in rows:
        i = r["index"]
        m = r["cost"].get(core)
        if i not in frag and i not in vert and not m:
            r["cost"] = {}
            priced.append(r)
            continue
        x = dict(m or {"px_price": None, "vtx_price": None, "px_bound": [], "vtx_bound": [], "px_path": None,
                       "vtx_path": None, "fragment": 0.0, "vertex": 0.0, "compute": 0.0, "flags": [],
                       "px_regs": None, "vtx_regs": None, "px_fp16": None})
        if i in frag:
            x.update(fragment=frag[i], px_bound=fb[i])
        if i in vert:
            x.update(vertex=vert[i], vtx_bound=vb[i])
            if r["pixels"] >= r["vertices"]:  # a fullscreen draw: the rest of its draw is raster work, not vertices
                x.update(fragment=x["fragment"] + vert[i], vertex=0.0)
        x["px_price"] = x["fragment"] / r["pixels"] if r["pixels"] else None
        x["vtx_price"] = x["vertex"] / r["vertices"] if r["vertices"] else None
        x["total"] = x["fragment"] + x["vertex"] + x["compute"]
        x["unmeasured"] = {p: why.get((i, p), "not_swapped") for p, done in
                           (("fragment", i in frag or not r["pixels"]), ("vertex", i in vert or i in frag
                                                                          and r["pixels"] >= r["vertices"]),
                            ("compute", False))
                           if not done and (m or {}).get(p)}
        if not r.get("geometry"):
            fallback += sum((m or {}).get(p) or 0.0 for p in x["unmeasured"])
        r["cost"] = {core: x}
        priced.append(r)
    done = {r["index"] for r in priced if r["cost"]}
    missing = [x for x in c["missing"] if x["index"] not in done]
    events_ = [r for r in priced if not r.get("geometry")] + [r for r in c["events"] if r["kind"] == "tile"]
    for r in events_:
        if r["kind"] == "tile":
            r["cost"] = {core: r["cost"][core]} if core in r["cost"] else {}
    geometry = [r for r in priced if r.get("geometry")]
    events_.sort(key=lambda r: -(r["cost"].get(core) or {}).get("total", 0.0))
    c["loops"]["unknown"] = sum(1 for r in events_ if _default_loop(r) and {"fragment", "compute"} & set(
        (r["cost"].get(core) or {}).get("unmeasured") or {}))  # a measured part does not use the loop's trip count
    c.update({"events": events_, "geometry": geometry, "missing": missing, "main_core": core,
              "cores": [x for x in c["cores"] if x["name"] == core] or [{"name": core, "arch": None}],
              "totals": _totals(events_, [core]),
              "measured": sc.get("source") or "renderdoc",
              "measured_fallback": {"events": sum(1 for r in events_ if (r["cost"].get(core) or {}).get("unmeasured")),
                                    "cycles": fallback}})
    return True
