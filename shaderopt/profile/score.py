"""Heaviness of a shader on one core (plan item A1.5, decision D-05).

score = cycles of the bottleneck pipe on the longest path (malioc cycles of that core:
compare cores of one class; across classes the pipes differ in width). The arithmetic pipe is malioc's
arith_total ("A"), not the busiest of FMA / CVT / SFU: on Mali-G57..G78 A = max(FMA, CVT, SFU), but on G610 / G710
the three share issue (A ≈ FMA + CVT + SFU/4) and on G715 / G720 A lies between the maximum and the sum.
The bound is named by the sub-pipe(s) equal to A when there are any, else "arith".

When malioc reports the longest path as N/A (dynamic loops, e.g. the URP / Forward+ light loops), the price of a
frame comes from frame/loops.py (the loops forced to n iterations); without it the fallback is the total cycles
(every instruction once: both sides of every branch, every loop body once — neither a lower nor an upper bound),
raised per pipe to the shortest path where that is larger (total counts spill traffic once), and the shader gets
the "dynamic_loop" flag. Vertex shaders (IDVS) count Position + Varying.

Flags:
  regs_gt32    more than 32 work registers in some variant: half thread occupancy
  spilling     registers spilled to the stack
  low_fp16     16-bit arithmetic below the threshold (default 25 %)
  sfu_bound    the bottleneck is the SFU pipe (transcendentals, divisions, integer ops)
  dynamic_loop longest path is N/A, the score is the total cycles (or the forced-loop price, see frame/loops.py)
"""
PIPES = ("fma", "cvt", "sfu", "ls", "v", "t", "arith")
ARITH_SUB = ("fma", "cvt", "sfu")
FP16_THRESHOLD = 25
FLAGS = ("regs_gt32", "spilling", "low_fp16", "sfu_bound", "dynamic_loop")


def add_cycles(a, b):
    if a is None or b is None:
        return None
    return {p: (a.get(p) or 0) + (b.get(p) or 0) for p in set(a) | set(b)}


def combined(rec):
    """{"longest", "shortest", "total"} cycles of the record: the main variant, or
    Position + Varying of a vertex shader (A1.4)."""
    vs = rec["variants"]
    if "main" in vs:
        v = vs["main"]
        return {k: v[k] for k in ("longest", "shortest", "total")}
    out = None
    for v in vs.values():
        cur = {k: v[k] for k in ("longest", "shortest", "total")}
        out = cur if out is None else {k: add_cycles(out[k], cur[k]) for k in cur}
    return out


def bottleneck(c):
    """(cycles, [bound pipes]) of a cycles dict. Bifrost reports only 'arith'; on Valhall and later 'arith' is
    malioc's arithmetic total and fma / cvt / sfu are its breakdown."""
    vals = {p: c[p] for p in PIPES if c.get(p) is not None}
    sub = {p: vals.pop(p) for p in ARITH_SUB if p in vals}
    if "arith" not in vals and sub:
        vals["arith"] = max(sub.values())
    if not vals:
        return 0.0, []
    top = max(vals.values())
    bound = [p for p, v in vals.items() if v >= top - 1e-9]
    if "arith" in bound and sub:
        named = [p for p, v in sub.items() if v >= vals["arith"] - 1e-9]
        if named:
            i = bound.index("arith")
            bound[i:i + 1] = named
    return top, bound


def fallback(c):
    """Cycles used when the longest path is N/A: total, raised per pipe to the shortest path."""
    t, s = c["total"] or {}, c["shortest"] or {}
    return {p: max(t.get(p) or 0, s.get(p) or 0) for p in set(t) | set(s)}


def score(rec, fp16_threshold=FP16_THRESHOLD, cycles=None, path=None):
    """Heaviness of one measured record (one file on one core). cycles / path: a price computed elsewhere
    (frame/loops.py: dynamic loops forced to n iterations), used instead of the record's own path."""
    if cycles is None:
        c = combined(rec)
        if c["longest"] is not None:
            cycles, path = c["longest"], "longest"
        else:
            cycles, path = fallback(c), "total"
    cyc, bound = bottleneck(cycles)
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
