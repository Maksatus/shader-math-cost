"""The function cost site (docs/): `python -m paretogpu bench` measures every function on every Mali GPU
(app/bench.py) -> mali_math_cost.csv, then builds the site's data (views/site.py) -> data.js, summary_*.csv.
"""
import csv
import os

from paretogpu.app import bench
from paretogpu.features.common import positive_int
from paretogpu.features.spec import Arg, Command
from paretogpu.store import workspace
from paretogpu.views import site
from paretogpu.views.reporter import CONSOLE as rep

DOCS = os.path.join(os.path.dirname(workspace.PKG), "docs")
ERRORS = os.path.join(workspace.OUT, "_bench", "errors.txt")


def run_command(args):
    if not args.site_only:
        rows, errors, warnings = bench.run(args.gpus, args.jobs, rep)
        os.makedirs(args.out, exist_ok=True)
        out = os.path.join(args.out, "mali_math_cost.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {os.path.abspath(out)}")
        if errors or warnings:
            os.makedirs(os.path.dirname(ERRORS), exist_ok=True)
            with open(ERRORS, "w", encoding="utf-8") as f:
                for e in errors:
                    f.write(" | ".join(map(str, e[:4])) + "\n" + e[4] + "\n\n")
                for w in warnings:
                    f.write("SUSPICIOUS " + w + "\n")
            if errors:
                print(f"{len(errors)} failed measurements -> {ERRORS}")
            if warnings:
                print(f"{len(warnings)} suspicious results, the cache may be corrupted: delete "
                      f"{os.path.join(bench.CACHE, 'malioc.sqlite')} and rerun -> {ERRORS}")
        elif os.path.exists(ERRORS):
            os.remove(ERRORS)
    rep.phase("site")
    site.build(args.out)
    return 0


BENCH = Command(
    "bench", "measure every function on every Mali GPU -> docs/mali_math_cost.csv, then the site's data.js and "
             "summary_*.csv",
    [Arg("--gpus", default="", help="comma separated GPUs (default: all)"),
     Arg("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)"),
     Arg("--out", default=DOCS, help="site folder (default: docs/)"),
     Arg("--site-only", action="store_true", help="only rebuild data.js and summary_*.csv from mali_math_cost.csv")],
    run_command, phases=lambda v: (["site"] if v.get("site_only") else ["bench_compile", "site"]))
