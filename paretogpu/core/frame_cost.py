"""Frame cost per core: draw = pixels x px_price + vertices x vtx_price, dispatch = threads x cs_price (core/pricing).
Units: malioc cycles x invocations; only cores of one generation compare in absolute numbers (pipe widths differ).
Vertex price = Position + Varying for every vertex: IDVS shades Varying only for visible ones, so it is an upper bound.
px_ref / vtx_ref / cs_ref = price with every outer loop once (REF_N): trip-count independent, used by core/compare.
"""
import math
import os
import re
from collections import OrderedDict, defaultdict

from paretogpu.core import loops
from paretogpu.core import pixels as px
from paretogpu.core import profile
from paretogpu.core import variants
from paretogpu.core.pricing import LOOP_ITERS, combined, loop_parts, price, price_draw
from paretogpu.model.cores import MAIN_CORE
from paretogpu.model.cost import CostRun, CostSummary
from paretogpu.model.frame import Event
from paretogpu.model.variant import VariantKey, VariantState

GROUPS = ("stage", "shader", "variant", "object", "rt")
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
    """Outer loop trip counts: uniform bound from params, else measured mean per pixel (frag only), else LOOP_ITERS."""

    def __init__(self, ev, loop_data, bounds, iters=None):
        self.ev, self.loop_data, self.bounds, self.n = ev, loop_data, bounds, LOOP_ITERS
        self.iters = iters

    def sources(self, fn):
        p = next(iter((self.loop_data.get(fn) or {}).values()), None)
        if not p:
            return None
        size = len(p["loops"])
        measured = self.iters if self.iters and len(self.iters["n"]) == size and fn.endswith(FRAG) else None
        out = []
        for k, b in enumerate(self.bounds.get(fn) or [None] * size):
            v = loops.bound_value(b, self.ev.get("params"))
            if v is not None:
                out.append({"n": max(0, math.ceil(v - 1e-6)), "from": "uniform", "bound": loops.bound_text(b),
                            "value": v})
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


NO_SHADER = {"cycles": 0.0, "bound": [], "path": None, "flags": [], "work_regs": None, "fp16_pct": None}


def _price_draw(row, recs, counts, loop_data):
    """recs with "vertex" alone (device depth-only draw): pixel price 0."""
    row["files"] = {s: next(iter(r.values()))["file"] for s, r in recs.items()}
    ff, fv = row["files"].get("fragment"), row["files"]["vertex"]
    n = {"fragment": counts.at(ff) if ff else 0, "vertex": counts.at(fv)}
    found = {}
    for c in recs.get("fragment") or recs["vertex"]:
        if c not in recs["vertex"]:
            continue
        if ff:
            d = price_draw(recs, row["files"], c, n, loop_data)
        else:
            v = price(recs["vertex"], fv, c, n["vertex"], loop_data)
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


def _unforced(loop_data, measurements, cores):
    """Files without loop data on a core where malioc's longest path is N/A (priced by total cycles there)."""
    def na(fn, c):
        r = (measurements.get(fn) or {}).get(c)
        return r is not None and r.get("ok", True) and combined(r)["longest"] is None
    return sorted(fn for fn, d in loop_data.items() if any(na(fn, c) and c not in d for c in cores))


def compute(meta: dict, events: list[Event], state: VariantState, cores=None,
            main_core=MAIN_CORE) -> CostRun:
    """-> frame_cost.json. An event priced on only some cores is in "missing" with "cores" = the unpriced ones."""
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
        counts = _Counts(ev, loop_data, bounds, iters.get(ev["index"]))
        if recs and "compute" in recs:
            found_cores.update(_price_compute(row, recs, counts, loop_data))
        elif recs and (len(recs) == 2 or (set(recs) == {"vertex"} and not why)):
            found_cores.update(_price_draw(row, recs, counts, loop_data))
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
    totals = _totals(rows, cores)
    rows.sort(key=lambda r: -(r["cost"].get(main_core, {}).get("total", -1)))
    draws = [r for r in rows if r["kind"] == "draw"]
    return {
        "frame": {k: v for k, v in meta.items() if k not in ("events", "pipeline")},
        "cores": [{"name": c, "arch": found_cores[c]} for c in cores], "main_core": main_core,
        "totals": totals,
        "loops": {"n": LOOP_ITERS, "events": sum(1 for r in rows if r["loop"]), "files": len(loop_data),
                  "bounded": sum(1 for r in rows if r["loop"] and any(x["from"] == "uniform"
                                                                         for x in r["loop"]["loops"] or [])),
                  "measured": sum(1 for r in rows if r["loop"] and any(x["from"] == "frame"
                                                                          for x in r["loop"]["loops"] or [])),
                  "unknown": sum(1 for r in rows if r["loop"] and (r["pixels"] or r["threads"]) and (
                      not r["loop"]["loops"] or any(x["from"] not in KNOWN for x in r["loop"]["loops"]))),
                  "unforced": _unforced(loop_data, state.get("measurements") or {}, cores)},
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


def summary(c: CostRun) -> CostSummary:
    frame,prof, cov = c.get("frame") or {}, c.get("profile") or {}, c.get("coverage") or {}
    fd = c.get("frame_dir") or ""
    mc = c.get("main_core")
    t = (c.get("totals") or {}).get(mc) or {}
    missing = [m for m in c.get("missing") or [] if not m.get("cores") or len(m["cores"]) == len(c.get("cores") or [])]
    failed = [m for m in missing if m.get("kind") == "draw"]
    top = (((c.get("groups") or {}).get(mc) or {}).get("shader") or [])[:5]
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
