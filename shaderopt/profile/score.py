"""Heaviness of a shader on one core (plan item A1.5, decision D-05).

score = cycles of the bottleneck pipe on the longest path (malioc cycles of that core:
compare cores of one generation; across generations the pipes differ in width); when malioc reports the
longest path as N/A (dynamic loops, e.g. the URP additional lights loop) the total
cycles are used instead (about one loop iteration) and the shader gets the
"dynamic_loop" flag. Vertex shaders (IDVS) count Position + Varying.

Flags:
  regs_gt32    more than 32 work registers in some variant: half thread occupancy
  spilling     registers spilled to the stack
  low_fp16     16-bit arithmetic below the threshold (default 25 %)
  sfu_bound    the bottleneck is the SFU pipe (transcendentals, divisions, integer ops)
  dynamic_loop longest path is N/A, the score is the total cycles
"""
PIPES = ("fma", "cvt", "sfu", "ls", "v", "t", "arith")
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
    """(cycles, [bound pipes]) of a cycles dict; Bifrost reports only 'arith'."""
    vals = {p: c[p] for p in PIPES if c.get(p) is not None}
    # 'arith' is the busiest of fma/cvt/sfu on Valhall and later: name the real pipe
    # (and ignore it, a Position + Varying sum of maxima can exceed every pipe sum)
    if "fma" in vals:
        vals.pop("arith", None)
    top = max(vals.values())
    return top, [p for p, v in vals.items() if v >= top - 1e-9]


def score(rec, fp16_threshold=FP16_THRESHOLD):
    """Heaviness of one measured record (one file on one core)."""
    c = combined(rec)
    path = "longest" if c["longest"] is not None else "total"
    cyc, bound = bottleneck(c[path])
    vs = rec["variants"].values()
    fp16 = [v["fp16_pct"] for v in vs if v["fp16_pct"] is not None]
    flags = {
        "regs_gt32": any((v["work_regs"] or 0) > 32 for v in vs),
        "spilling": any(v["spilling"] for v in vs),
        "low_fp16": bool(fp16) and min(fp16) < fp16_threshold,
        "sfu_bound": "sfu" in bound,
        "dynamic_loop": path == "total",
    }
    return {
        "cycles": round(cyc, 4), "path": path, "bound": bound,
        "work_regs": max((v["work_regs"] or 0) for v in vs),
        "fp16_pct": min(fp16) if fp16 else None,
        "flags": [f for f in FLAGS if flags[f]],
    }
