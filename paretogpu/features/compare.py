"""Two cost runs against each other (plan items K2.1, A5.1): `python -m paretogpu compare <A> <B>`.

The frame, its stages, shaders, variants and events; every change split into price (the shaders), work (pixels,
vertices) and mix (variants) by core/compare.py -> compare.html, compare.json.
"""
import os
import sys

from paretogpu.core import compare
from paretogpu.features.spec import Arg, Command
from paretogpu.store import cost_runs
from paretogpu.views import html


def run_command(args):
    try:
        a, b = cost_runs.load(args.a), cost_runs.load(args.b)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot read a cost run: {e}")
    cmp = compare.compare(a, b)
    for w in cmp["warnings"]:
        print(f"  ! {w}")
    x = cmp["by_core"].get(cmp["main_core"])
    if x:
        pct = f" ({100 * x['delta'] / x['a']:+.1f}%)" if x["a"] else ""
        print(f"{x['core']}: {x['a'] / 1e6:.1f} -> {x['b'] / 1e6:.1f} M cycles, {x['delta'] / 1e6:+.2f} M{pct}: "
              f"price {x['price'] / 1e6:+.2f}, work {x['work'] / 1e6:+.2f}, mix {x['mix'] / 1e6:+.2f}"
              + ("  (the same snapshot: only prices differ)" if cmp["same_frame"] else ""))
        for s in x["shaders"][:10]:
            if abs(s["delta"]) < 1:
                break
            print(f"  {s['delta'] / 1e6:+8.2f} M  {s['shader'][:50]:50s} price {s['price'] / 1e6:+.2f}, "
                  f"work {s['work'] / 1e6:+.2f}, mix {s['mix'] / 1e6:+.2f}" + (f"  [{s['status']}]" if s["status"] != "both" else ""))
    out = args.out or (args.b if os.path.isdir(args.b) else os.path.dirname(os.path.abspath(args.b)))
    print(f"-> {os.path.abspath(html.write_result(cmp, out, 'compare'))}")
    return 0


COMPARE = Command(
    "compare", "compare two cost runs: the frame, its stages and shaders, every change split into price (the shaders), "
               "work (pixels, vertices) and mix (variants)",
    [Arg("a", help="before: snapshot folder (its latest cost), frame_cost.json or <snapshot>/costs/<time>.json"),
     Arg("b", help="after: the same kinds"),
     Arg("--out", help="folder for compare.html and compare.json (default: B's folder)")],
    run_command)
