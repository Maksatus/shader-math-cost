"""Shaders without a frame (plan items A1, A2.2): `measure` a folder of GLSL / SPIR-V files, `report` it, `export`
the shaders of a Unity project into such a folder (adapters/unity/export.py) and measure it.
"""
import os
import sys

from paretogpu.adapters.unity import export as unity_export
from paretogpu.adapters.unity.cli import UnityError
from paretogpu.app import measure
from paretogpu.features.common import FP16, JOBS, MAIN_CORES, fail, parse_cores
from paretogpu.features.spec import Arg, Command
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE as rep


def make_report(folder, out, args):
    rows, cores, _ = measure.report(folder, out, args.fp16_threshold)
    out = out or folder
    for stage in ("fragment", "vertex"):
        top = [r for r in rows if r["stage"] == stage][:3]
        if top:
            print(f"top {stage}: " + "; ".join(f"{r['shader']} {r['pass']} {' '.join(r['keywords']) or '-'} "
                                               f"[{r['api']}] {r['main']['cycles']} cycles on {r['main']['core']}" for r in top))
    print(f"-> {os.path.abspath(os.path.join(out, 'report.csv'))}")


def measure_folder(folder, cores, api, out=None, jobs=None):
    """Measure and print a summary; returns the number of failed shaders, or None if there were none."""
    out = out or folder
    records, failures = measure.run(folder, cores, api, out, jobs)
    n = len(records) + len(failures)
    if not n:
        return None
    cached = sum(c for _, c in records)
    print(f"{n} measurements ({n // len(cores)} shaders x {len(cores)} cores), {len(records)} ok, "
          f"{len(failures)} failed ({cached} from cache); GLSL as {api}, SPIR-V as vulkan")
    for rel, err in failures:
        print(f"FAIL {rel}\n{err}\n")
    print(f"-> {os.path.abspath(os.path.join(out, 'measurements.csv'))}")
    print(f"-> {os.path.abspath(os.path.join(out, 'measurements.jsonl'))}")
    return len(failures)


def run_measure(args):
    if not os.path.isdir(args.folder):
        sys.exit(f"not a folder: {args.folder}")
    cores = parse_cores(args)
    failed = measure_folder(args.folder, cores, args.api, args.out, args.jobs)
    if failed is None:
        sys.exit(f"no .vert / .frag / .vert.spv / .frag.spv files in {args.folder}")
    if not args.no_report:
        make_report(args.out or args.folder, None, args)
    return 1 if failed else 0


def run_report(args):
    if not os.path.exists(os.path.join(args.folder, "measurements.jsonl")):
        sys.exit(f"no measurements.jsonl in {args.folder}: run `measure` first")
    make_report(args.folder, args.out, args)
    return 0


def run_export(args):
    cores = parse_cores(args) if args.measure else None
    out = args.out or workspace.export_dir(args.project)
    try:
        rep.phase("unity_export")
        res = unity_export.export(args.project, args.shaders, out, args.platforms.split(","), args.mode, args.timeout)
    except UnityError as e:
        fail(f"export failed: {e}", e)
    print(f"Unity {res.get('unity')} ({res['mode']}, {res['seconds']} s)")
    bad = len(res.get("errors", []))
    for e in res.get("errors", []):
        print(f"FAIL {e}")
    for item in res.get("items", []):
        if item.get("error"):
            bad += 1
            print(f"FAIL {item['shader']} ({item['asset']}): {item['error']}")
            continue
        for d, n in item.get("outputs", {}).items():
            print(f"{item['shader']}: {n} files -> {d}")
        for e in item.get("split_errors", []):
            bad += 1
            print(f"  FAIL {e}")
    if args.measure:  # the whole output folder: GLSL as GLES, SPIR-V as Vulkan, one report
        print()
        bad += measure_folder(out, cores, "gles", jobs=args.jobs) or 0
        make_report(out, None, args)
    return 1 if bad else 0


MEASURE = Command(
    "measure", "measure every .vert / .frag (GLSL) and .vert.spv / .frag.spv (SPIR-V, Vulkan only) in a folder with "
               "malioc, then write the report",
    [Arg("folder"), MAIN_CORES, JOBS,
     Arg("--api", default="gles", choices=["gles", "vulkan"], help="API for GLSL files (SPIR-V is always vulkan)"),
     Arg("--out", help="output folder (default: the measured folder)"),
     Arg("--no-report", action="store_true", help="skip report.csv"),
     FP16],
    run_measure, phases=lambda v: ["measure"])

REPORT = Command(
    "report", "report.csv (variants ranked on the main core) from measurements.jsonl",
    [Arg("folder"), Arg("--out", help="output folder (default: the folder)"), FP16],
    run_report)

EXPORT = Command(
    "export", "compile shaders of a Unity project into per-variant GLSL / SPIR-V files (in the open editor via Unity "
              "CLI, else in batchmode)",
    [Arg("shaders", nargs="+", help="asset path (Assets/.../X.shadergraph), folder under Assets/ or shader name "
                                    "(Universal Render Pipeline/Lit)"),
     Arg("--project", required=True, help="Unity project folder"),
     Arg("--out", help="output folder (default: paretogpu/corpus/real/<project name>, not in git)"),
     Arg("--platforms", default="gles3,vulkan", help="comma separated: gles3, vulkan"),
     Arg("--mode", default="auto", choices=["auto", "editor", "batch"],
         help="auto: the open editor if it answers Unity CLI, else batchmode"),
     Arg("--timeout", type=int, default=1800, help="seconds"),
     Arg("--measure", action="store_true", help="measure the output folder with malioc and write the report"),
     MAIN_CORES, JOBS, FP16],
    run_export, phases=lambda v: ["unity_export"] + (["measure"] if v.get("measure") else []))
