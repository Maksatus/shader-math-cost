"""Shader price on one core = sum of the pipes' cycles on malioc's longest path (compare cores of one class only). On
the hardware a shader's time is near its busiest pipe (Pixel 7 knockouts), but malioc splits the work between pipes
otherwise: its busiest pipe ranked VATyakov's shaders x0.34..1.24 against the phone, its sum x1.1..1.45.
Arith = malioc's arith_total "A", not max(FMA, CVT, SFU): equal on G57..G78, but G610 / G710 share issue
(A ~ FMA + CVT + SFU/4) and G715 / G720 lie between max and sum. Longest path N/A (dynamic loops): core/loops forces
n iterations, else total cycles raised per pipe to the shortest path (total counts spills once) + "dynamic_loop".
regs_gt32 = half thread occupancy.
LS: malioc charges every access as one lane per cycle; G78's counters show coalesced accesses (16 bytes per lane, a
64-byte path: 4 lanes per cycle), so Valhall and later price LS / LS_LANES (K3.1).
"""
from paretogpu.core import loops
from paretogpu.model.measurement import MALI, Measurement, PipeModel, Score

PIPES = MALI.pipes
FP16_THRESHOLD = 25
FLAGS = ("regs_gt32", "spilling", "low_fp16", "sfu_bound", "dynamic_loop")
LOOP_ITERS = 2            # default dynamic loop trip count (lights, probes, ray steps)
LOOP_NS = (0, 1, 2, 4, 8)  # trip counts of the range in the report
LS_LANES = 4


def add_cycles(a, b):
    if a is None or b is None:
        return None
    return {p: (a.get(p) or 0) + (b.get(p) or 0) for p in set(a) | set(b)}


def combined(rec: Measurement, model: PipeModel = MALI):
    """-> {"longest", "shortest", "total"}: the main variant, else the sum of all (vertex: Position + Varying)."""
    vs = rec["variants"]
    if model.main_variant in vs:
        v = vs[model.main_variant]
        return {k: v[k] for k in ("longest", "shortest", "total")}
    out = None
    for v in vs.values():
        cur = {k: v[k] for k in ("longest", "shortest", "total")}
        out = cur if out is None else {k: add_cycles(out[k], cur[k]) for k in cur}
    return out


def raised(c, model: PipeModel = MALI):
    """c with 'arith' raised to its busiest part: malioc's arith is never below it, per-pipe loop interpolation
    (core/loops.cycles_at) can put it there."""
    sub = [c[p] for p in model.arith_sub if c.get(p) is not None]
    return {**c, model.arith: max(c.get(model.arith) or 0.0, *sub)} if sub else c


def pipe_cycles(c, model: PipeModel = MALI):
    """-> (sum of the pipes' cycles, [busiest pipes]). Bifrost reports only 'arith'; on Valhall+ 'arith' is the total and
    fma / cvt / sfu its breakdown, named as the busiest when equal to it."""
    arith = model.arith
    sub = {p: c[p] for p in model.arith_sub if c.get(p) is not None}
    r = raised(c, model)
    vals = {p: r[p] for p in model.pipes if p not in model.arith_sub and r.get(p) is not None}
    if not vals:
        return 0.0, []
    top = max(vals.values())
    bound = [p for p, v in vals.items() if v >= top - 1e-9]
    if arith in bound and sub:
        named = [p for p, v in sub.items() if v >= vals[arith] - 1e-9]
        if named:
            i = bound.index(arith)
            bound[i:i + 1] = named
    return sum(vals.values()), bound


def coalesced(c, arch):
    return c if arch == "Bifrost" or not c.get("ls") else {**c, "ls": c["ls"] / LS_LANES}


def fallback(c):
    t,s = c["total"] or {}, c["shortest"] or {}
    return {p: max(t.get(p) or 0, s.get(p) or 0) for p in set(t) | set(s)}


def score(rec: Measurement, fp16_threshold=FP16_THRESHOLD, cycles=None, path=None) -> Score:
    """cycles / path: precomputed (core/loops forced loops), used instead of the record's own path."""
    if cycles is None:
        c = combined(rec)
        if c["longest"] is not None:
            cycles, path = c["longest"], "longest"
        else:
            cycles, path = fallback(c), "total"
    cyc, bound = pipe_cycles(coalesced(cycles, rec.get("arch")))
    vs = rec["variants"].values()
    fp16 = [v["fp16_pct"] for v in vs if v["fp16_pct"] is not None]
    flags = {
        "regs_gt32": any((v["work_regs"] or 0) > 32 for v in vs),
        "spilling": any(v["spilling"] for v in vs),
        "low_fp16": bool(fp16) and min(fp16) < fp16_threshold,
        "sfu_bound": "sfu" in bound,
        "dynamic_loop": combined(rec)["longest"] is None,
    }
    return {
        "cycles": round(cyc, 4), "path": path, "bound": bound,
        "work_regs": max((v["work_regs"] or 0) for v in vs),
        "fp16_pct": min(fp16) if fp16 else None,
        "flags": [f for f in FLAGS if flags[f]],
    }


def _n_text(n):
    return "/".join(f"{round(x, 2):g}" for x in n) if isinstance(n, (list, tuple)) else str(n)


def price(recs: dict, file, core, n, loop_data, fp16_threshold=FP16_THRESHOLD) -> Score:
    """n: one trip count for every outer loop, or a list with one per loop (core/loops)."""
    p = (loop_data.get(file) or {}).get(core)
    if p:
        return score(recs[core], fp16_threshold, loops.cycles_at(p, n), f"loop n={_n_text(n)}")
    return score(recs[core], fp16_threshold)


def loop_parts(recs: dict, file, core, n, loop_data, fp16_threshold=FP16_THRESHOLD) -> list | None:
    """-> [{"n", "cycles"}] per outer loop in code order, cycles = price minus price with that loop at 0; None without loop data."""
    p = (loop_data.get(file) or {}).get(core)
    if not p:
        return None
    ns = loops.per_loop(n, len(p["loops"]))
    full = price(recs, file, core, ns, loop_data, fp16_threshold)["cycles"]
    return [{"n": m, "cycles": round(full - price(recs, file, core, ns[:k] + [0] + ns[k + 1:], loop_data,
                                                    fp16_threshold)["cycles"], 4)} for k, m in enumerate(ns)]


def price_draw(recs: dict, files: dict, core, n, loop_data, fp16_threshold=FP16_THRESHOLD) -> dict:
    """-> {"px": Score, "vtx": Score, "flags": merged, minus vertex low_fp16 (vertex shaders need little fp16)}.
    recs: core/variants.match records; n: for both stages, or {"fragment": n, "vertex": n}."""
    nf, nv = (n["fragment"], n["vertex"]) if isinstance(n, dict) else (n, n)
    px = price(recs["fragment"], files["fragment"], core, nf, loop_data, fp16_threshold)
    vtx = price(recs["vertex"], files["vertex"], core, nv, loop_data, fp16_threshold)
    return {"px": px, "vtx": vtx, "flags": sorted(set(px["flags"]) | {x for x in vtx["flags"] if x != "low_fp16"})}
