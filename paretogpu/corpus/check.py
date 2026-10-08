"""Compile the corpus with malioc and compare with expected.json (plan item A0.1).

Checks the synthetic shaders against expected.json and that every shader in
real/ (not in git) compiles. Exit code 1 if anything fails.

Usage: python check.py [--core Mali-G78] [--api gles]
"""
import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from paretogpu import mali
from paretogpu.profile.score import score

PIPES = ("fma", "cvt", "sfu", "ls", "v", "t", "arith")


def bound_cycles(c):
    return max(v for p, v in c.items() if p in PIPES and v is not None)


def summary(v):
    """Bottleneck cycles of a variant; total cycles when the longest path is N/A."""
    if v["longest"] is None:
        return f"longest N/A, total {bound_cycles(v['total']):.2f} r{v['work_regs']}"
    return f"{bound_cycles(v['longest']):.2f} {'+'.join(v['bound'])} r{v['work_regs']}"


def value(recs, ref):
    name, variant, what = ref
    v = recs[name]["variants"][variant]
    return bound_cycles(v["longest"]) if what == "bound_cycles" else v["longest"][what]


def run_check(name, chk, recs):
    """-> (ok, text)"""
    if "greater" in chk:
        a, b = value(recs, chk["greater"]), value(recs, chk["than"])
        return a - b >= chk["by"], f"{'/'.join(chk['greater'])} {a:.3f} - {'/'.join(chk['than'])} {b:.3f} >= {chk['by']:.3f}"
    if "equal" in chk:
        a, b = value(recs, chk["equal"]), value(recs, chk["to"])
        return abs(a - b) <= chk["tol"] * max(b, 1e-9), f"{'/'.join(chk['equal'])} {a:.3f} ~ {b:.3f}"
    v = recs[name]["variants"][chk["variant"]]
    if "spilling" in chk:
        return v["spilling"] == chk["spilling"], f"spilling {v['spilling']} (want {chk['spilling']})"
    if "bound" in chk:
        return sorted(v["bound"]) == sorted(chk["bound"]), f"bound {v['bound']} (want {chk['bound']})"
    got = v[chk["path"]]
    if "na" in chk:
        return (got is None) == chk["na"], f"{chk['path']} {'N/A' if got is None else 'present'} (want {'N/A' if chk['na'] else 'present'})"
    if "max_bound_cycles" in chk:
        b = bound_cycles(got)
        return b <= chk["max_bound_cycles"], f"{chk['path']} bound {b:.3f} <= {chk['max_bound_cycles']:.3f}"
    bad = []
    eb, gb = bound_cycles(chk["cycles"]), bound_cycles({p: got[p] for p in chk["cycles"]})
    for p, e in chk["cycles"].items():
        # only the bottleneck pipes set the cost (the table clamps negative per-call costs to 0,
        # so it overestimates pipes that a function unloads)
        if max(e, got[p]) >= 0.9 * eb and abs(got[p] - e) > chk["tol"] * e:
            bad.append(f"{p} {got[p]:.3f} vs {e:.3f}")
    if abs(gb - eb) > chk["tol"] * eb:
        bad.append(f"bound {gb:.3f} vs {eb:.3f}")
    if bound_cycles(got) > gb:
        bad.append(f"bound by {'+'.join(v['bound'])}, not by the table pipes")
    return not bad, (f"{chk['path']} bound {gb:.3f} vs table {eb:.3f} ({(gb / eb - 1) * 100:+.1f}%)"
                     + ("; " + ", ".join(bad) if bad else ""))


def main():
    exp = json.load(open(os.path.join(HERE, "expected.json"), encoding="utf-8"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default=exp["core"])
    ap.add_argument("--api", default=exp["api"])
    args = ap.parse_args()

    installed = mali.version()
    if installed != exp["malioc"]:
        sys.exit(f"expected.json was made with malioc {exp['malioc']}, installed is {installed} ({mali.MALIOC}): "
                 "check the new numbers and regenerate it with python -m paretogpu.corpus.gen_synthetic")
    fails = 0
    recs = {}
    for name in exp["shaders"]:
        src = open(os.path.join(HERE, "synthetic", name)).read()
        r = mali.measure(src, args.core, args.api, mali.STAGES[os.path.splitext(name)[1]])
        if not r["ok"]:
            print(f"FAIL {name}: does not compile\n{r['error']}")
            fails += 1
            continue
        recs[name] = r

    for name, e in exp["shaders"].items():
        if name not in recs:
            continue
        vs = recs[name]["variants"]
        print(f"{name:22s} " + "  ".join(f"{vn}: {summary(v)}" for vn, v in vs.items()))
        for chk in e["checks"]:
            ok, text = run_check(name, chk, recs)
            fails += not ok
            print(f"  {'ok  ' if ok else 'FAIL'} {text}")

    # the heaviness score of the report (A1.5) must rank the corpus as expected
    got = sorted((n for n in exp["order_fragment"] if n in recs), key=lambda n: -score(recs[n])["cycles"])
    # shaders with equal expected cost may swap places
    cost = {n: exp["shaders"][n]["expected_bound_cycles"] for n in got}
    ok = all(cost[a] >= cost[b] * 0.9 for a, b in zip(got, got[1:]))
    fails += not ok
    print(f"{'ok  ' if ok else 'FAIL'} order: " + ", ".join(got))

    for path in sorted(glob.glob(os.path.join(HERE, "real", "**", "*.*"), recursive=True)):
        stage = mali.stage_of(path)
        if not stage:
            continue
        src = mali.read_source(path)
        r = mali.measure(src, args.core, "vulkan" if isinstance(src, bytes) else args.api, stage)
        rel = os.path.relpath(path, HERE)
        if not r["ok"]:
            print(f"FAIL {rel}: does not compile\n{r['error']}")
            fails += 1
        else:
            print(f"ok   {rel}: " + "  ".join(f"{vn}: {summary(v)}" for vn, v in r["variants"].items()))

    print(f"\n{fails} failed" if fails else "\nall checks passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
