"""Accuracy check of a one-argument approximation by exhaustive enumeration.

Every float32 of the domain (in chunks, on threads) and every float16 value, each operation rounded to the type,
with and without fma. The reference is the float64 function of the same rounded argument. With fma the product is
exact in float64 and the sum is rounded to float64 and then to the type (double rounding: rare, at most 1 ulp).

Reports:
- max absolute and relative error and the argument where it is reached;
- values outside the range of the function (|sin| > 1) and sign changes against the reference, as runs of
  consecutive arguments;
- beyond the domain: the first argument on each side where the error exceeds the tolerance, error at probe points;
- edges of the domain, 0, ±inf, NaN, denormals (also with denormals flushed to zero).

  check(approx, ref, domain, dt, ...) -> dict    approx(x, dt, fma) -> values of type dt

The command line checks approximations of build_approx.py with its settings and cache.
Usage: python check.py [FastSin FastSin5 FastSinAny] [--no-f32] [--json out.json]
"""
import argparse
import concurrent.futures as cf
import json
import os
import time

import numpy as np

BITS = {np.float32: (np.uint32, 0x80000000), np.float16: (np.uint16, 0x8000)}
CHUNK = 1 << 22
MAX_RUNS = 200


def key(v, dt):
    """Monotone integer index of a value of type dt: ..., -min_denormal -> -2, -0 -> -1, +0 -> 0, ..."""
    u, s = BITS[dt]
    with np.errstate(over="ignore"):
        b = int(np.array(v, dt).view(u))
    return -(b & (s - 1)) - 1 if b & s else b


def unkey(k, dt):
    u, s = BITS[dt]
    return np.where(k >= 0, k, (-k - 1) | s).astype(u).view(dt)


def domain_keys(lo, hi, dt):
    """Keys of the first and the last value of type dt inside [lo, hi]."""
    k0, k1 = key(lo, dt), key(hi, dt)
    return k0 + (float(dt(lo)) < lo), k1 - (float(dt(hi)) > hi)


def _runs(mask, k0, x, stride=1):
    """Runs of consecutive true values: [key first, key last, x first, x last, count]."""
    i = np.flatnonzero(mask)
    if not len(i):
        return []
    cut = np.flatnonzero(np.diff(i) > 1)
    a, b = np.r_[i[0], i[cut + 1]], np.r_[i[cut], i[-1]]
    return [[k0 + int(p) * stride, k0 + int(q) * stride, float(x[p]), float(x[q]), int(q - p + 1)] for p, q in zip(a, b)]


def _stats(x, y, r, k0, vrange, stride=1):
    e = y - r
    ae = np.where(np.isfinite(y), np.abs(e), np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        re = np.where(r != 0, ae / np.abs(r), np.where(y == 0, 0.0, np.inf))
    at = lambda v, i: [float(v[i]), float(x[i]), float(y[i]), float(r[i])]
    s = {"n": len(x), "nonfinite": int(np.count_nonzero(~np.isfinite(y))),
         "abs": at(ae, np.argmax(ae)), "rel": at(re, np.argmax(re)),
         "ymax": at(y, np.argmax(y)), "ymin": at(-y, np.argmax(-y)),
         "sign": _runs(np.sign(y) != np.sign(r), k0, x, stride)}
    s["ymin"][0] = -s["ymin"][0]
    if vrange:
        s["over"] = _runs(y > vrange[1], k0, x, stride)
        s["under"] = _runs(y < vrange[0], k0, x, stride)
    return s


def _merge(acc, s, stride=1):
    if acc is None:
        return s
    acc["n"] += s["n"]
    acc["nonfinite"] += s["nonfinite"]
    for k, better in (("abs", 1), ("rel", 1), ("ymax", 1), ("ymin", -1)):
        if better * s[k][0] > better * acc[k][0]:
            acc[k] = s[k]
    for k in ("sign", "over", "under"):
        if k not in s:
            continue
        a, b = acc[k], s[k]
        if a and b and a[-1][1] + stride == b[0][0]:
            a[-1] = [a[-1][0], b[0][1], a[-1][2], b[0][3], a[-1][4] + b[0][4]]
            b = b[1:]
        acc[k] = a + b
    return acc


def _summary(s, stride=1):
    """Counts of a sample (stride > 1) are scaled to the whole domain."""
    for k in ("sign", "over", "under"):
        if k in s:
            runs = s[k]
            s[k] = {"count": sum(r[4] for r in runs) * stride, "runs": len(runs),
                    "list": [{"x": [r[2], r[3]], "count": r[4]} for r in runs[:MAX_RUNS]]}
    return s


def _chunk(approx, ref, dt, fmas, k0, k1, vrange, stride=1):
    x = unkey(np.arange(k0, k1, stride, dtype=np.int64), dt)
    xd = x.astype(np.float64)
    r = ref(xd)
    with np.errstate(all="ignore"):
        return [_stats(xd, approx(x, dt, fma).astype(np.float64), r, k0, vrange, stride) for fma in fmas]


def _pool():
    return cf.ThreadPoolExecutor(min(8, os.cpu_count() or 1))


def enumerate_domain(approx, ref, lo, hi, dt, vrange=None, fmas=(False, True), chunk=CHUNK, stride=1):
    """Every value of type dt in [lo, hi] (every stride-th for a quick draft): {fma: stats}."""
    k0, k1 = domain_keys(lo, hi, dt)
    acc = [None] * len(fmas)
    with _pool() as ex:
        step = chunk * stride
        ranges = [(a, min(a + step, k1 + 1)) for a in range(k0, k1 + 1, step)]
        for res in ex.map(lambda ab: _chunk(approx, ref, dt, fmas, *ab, vrange, stride), ranges):
            acc = [_merge(a, s, stride) for a, s in zip(acc, res)]
    return {fma: _summary(a, stride) for fma, a in zip(fmas, acc)}


def first_exceed(approx, ref, lo, hi, dt, tol, limit, fma=False, chunk=CHUNK):
    """Nearest values of type dt outside [lo, hi] (|x| <= limit) where |error| > tol: (below, above)."""
    k0, k1 = domain_keys(lo, hi, dt)
    lim_lo, lim_hi = key(-limit, dt), key(limit, dt)

    def err(a, b):
        x = unkey(np.arange(a, b, dtype=np.int64), dt)
        with np.errstate(all="ignore"):
            y = approx(x, dt, fma).astype(np.float64)
            return x, ~(np.abs(y - ref(x.astype(np.float64))) <= tol)

    def scan(ranges, pick):
        with _pool() as ex:
            for i in range(0, len(ranges), ex._max_workers):
                for x, bad in ex.map(lambda ab: err(*ab), ranges[i:i + ex._max_workers]):
                    j = np.flatnonzero(bad)
                    if len(j):
                        return float(x[j[pick]])
        return None

    up = [(a, min(a + chunk, lim_hi + 1)) for a in range(k1 + 1, lim_hi + 1, chunk)]
    down = [(max(b - chunk, lim_lo), b) for b in range(k0, lim_lo, -chunk)]
    return scan(down, -1), scan(up, 0)


def special_values(dt, lo, hi):
    fi = np.finfo(dt)
    tiny, dmin = float(fi.smallest_normal), float(fi.smallest_subnormal)
    dmax = float(np.nextafter(dt(tiny), dt(0)))
    k0, k1 = domain_keys(lo, hi, dt)
    edges = [float(unkey(np.array([k]), dt)[0]) for k in (k0 - 1, k0, k1, k1 + 1)]
    v = [-np.inf, -float(fi.max), edges[0], edges[1], -tiny, -dmax, -dmin, -0.0,
         0.0, dmin, dmax, tiny, edges[2], edges[3], float(fi.max), np.inf, np.nan]
    names = ["-inf", "-max", "lo - 1 ulp", "lo", "-min normal", "-max denormal", "-min denormal", "-0",
             "+0", "min denormal", "max denormal", "min normal", "hi", "hi + 1 ulp", "max", "+inf", "nan"]
    return names, np.array(v, dt)


def specials(approx, ref, lo, hi, dt):
    names, x = special_values(dt, lo, hi)
    tiny = np.finfo(dt).smallest_normal
    ftz = lambda v: np.where(np.abs(v) < tiny, np.copysign(0, v), v).astype(v.dtype)
    with np.errstate(all="ignore"):
        y = approx(x, dt).astype(np.float64)
        yf = ftz(approx(ftz(x), dt)).astype(np.float64)
        r = ref(x.astype(np.float64))
        rel = np.where(r != 0, (y - r) / np.abs(r), np.nan)
    return [{"name": n, "x": float(a), "y": float(b), "y_ftz": float(c), "ref": float(d), "err": float(b - d),
             "rel": float(e)} for n, a, b, c, d, e in zip(names, x, y, yf, r, rel)]


def probes(approx, ref, xs, dt=np.float32):
    with np.errstate(all="ignore"):
        x = np.array(xs, dt)
        y = approx(x, dt).astype(np.float64)
        r = ref(x.astype(np.float64))
    return [{"x": float(a), "y": float(b), "ref": float(c), "err": float(b - c)} for a, b, c in zip(x, y, r)]


def check(approx, ref, domain, dt, vrange=None, tol=None, beyond=1e5, probe_x=(), chunk=CHUNK, stride=1):
    lo, hi = domain
    t = time.perf_counter()
    out = {"type": np.dtype(dt).name, "domain": [lo, hi], "range": vrange, "tol": tol, "stride": stride,
           "fma": {str(k).lower(): v for k, v in
                   enumerate_domain(approx, ref, lo, hi, dt, vrange, chunk=chunk, stride=stride).items()}}
    if tol:
        out["exceed"] = {"limit": beyond, "fma": {}}
        for fma in (False, True):
            out["exceed"]["fma"][str(fma).lower()] = first_exceed(approx, ref, lo, hi, dt, tol, beyond, fma, chunk)
    out["specials"] = specials(approx, ref, lo, hi, dt)
    out["probes"] = probes(approx, ref, probe_x, dt) if probe_x else []
    out["seconds"] = round(time.perf_counter() - t, 1)
    return out


def report(name, c):
    g = lambda v: f"{v:.5g}"
    print(f"\n=== {name}: {c['type']}, x in [{g(c['domain'][0])}, {g(c['domain'][1])}], {c['seconds']} s")
    for fma, s in c["fma"].items():
        print(f"  {'fma' if fma == 'true' else 'no fma'}: {s['n']:,} values, nonfinite results {s['nonfinite']}")
        print(f"    max abs error {s['abs'][0]:.5g} at x = {g(s['abs'][1])} (y {g(s['abs'][2])}, ref {g(s['abs'][3])})"
              + (f", {s['abs'][0] / c['tol']:.0%} of tolerance {c['tol']:.3g}" if c["tol"] else ""))
        print(f"    max rel error {s['rel'][0]:.5g} at x = {g(s['rel'][1])} (ref {g(s['rel'][3])})")
        print(f"    values in [{s['ymin'][0]:.8g}, {s['ymax'][0]:.8g}]: min at x = {g(s['ymin'][1])}, max at x = {g(s['ymax'][1])}")
        for k, what in (("over", f"> {c['range'][1]:g}" if c["range"] else ""),
                        ("under", f"< {c['range'][0]:g}" if c["range"] else ""), ("sign", "wrong sign")):
            if k not in s:
                continue
            r, h = s[k], lambda v: f"{v:.8g}"
            runs = "; ".join(f"[{h(q['x'][0])}, {h(q['x'][1])}] {q['count']:,}" for q in r["list"])
            span = f"x in [{h(r['list'][0]['x'][0])}, {h(r['list'][-1]['x'][1])}]" if r["list"] else ""
            print(f"    {what}: {r['count']:,} values in {r['runs']} runs" + (f": {runs}" if 0 < r["runs"] <= 4 else f", {span}" if span else ""))
    if "exceed" in c:
        for fma, (a, b) in c["exceed"]["fma"].items():
            print(f"  error > {c['tol']:.3g} outside the domain ({'fma' if fma == 'true' else 'no fma'}, |x| <= {c['exceed']['limit']:g}):"
                  f" below at x = {g(a) if a is not None else '-'}, above at x = {g(b) if b is not None else '-'}")
    if c["probes"]:
        print("  probes: " + ", ".join(f"x = {g(p['x'])}: y {g(p['y'])}, error {p['err']:.3g}" for p in c["probes"]))
    print("  special values (no fma; y_ftz: denormals flushed to zero on input and output):")
    for p in c["specials"]:
        print(f"    {p['name']:>14} {p['x']:>14.7g} -> {p['y']:>14.7g}  ftz {p['y_ftz']:>12.7g}  ref {p['ref']:>12.7g}"
              f"  err {p['err']:>11.4g}  rel {p['rel']:>11.4g}")


def main():
    from build_approx import APPROX, run_check

    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", default=["FastSin"], help="names or ids from build_approx.APPROX")
    ap.add_argument("--no-f32", action="store_true", help="float16 only (seconds instead of minutes)")
    ap.add_argument("--json", help="write the results to this file")
    args = ap.parse_args()
    out = {}
    for n in args.names:
        a = next(a for a in APPROX if n in (a["name"], a["id"]))
        out[a["name"]] = {}
        for dt, v in ((np.float32, "float"), (np.float16, "half")):
            if dt is np.float32 and args.no_f32:
                continue
            c = run_check(a, dt)
            report(a["name"], c)
            out[a["name"]][v] = c
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump(out, fp, indent=1)


if __name__ == "__main__":
    main()
