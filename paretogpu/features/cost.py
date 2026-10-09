"""The cost of a frame snapshot (plan items K1.4–K1.9, K1.13): `python -m paretogpu cost <snapshot>`.

Every event's exact shader variant is compiled (app/variants.py: checked against the shaders in the open editor) and
measured with malioc, dynamic loops are priced at n iterations, and the frame is priced (core/frame_cost.py):
frame_cost.json / .csv, frame_report.html, and the run is kept in <snapshot>/costs/ (store/cost_runs.py) to compare
with. --materials adds the shader variants of every material of the project ("project shaders" tab).
"""
import json
import os
import sys
import time

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import materials as project_materials
from paretogpu.app import results
from paretogpu.app import variants
from paretogpu.core import frame_cost
from paretogpu.core import materials as shaders
from paretogpu.core.pricing import LOOP_ITERS, LOOP_NS
from paretogpu.features.common import JOBS, cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.model.cores import main_core as main_core_of
from paretogpu.model.variant import PLATFORM
from paretogpu.store import cost_runs, workspace
from paretogpu.views import html, tables
from paretogpu.views.reporter import CONSOLE as rep


def run_command(args):
    path = os.path.join(args.frame, workspace.SNAPSHOT_FILE)
    if not os.path.exists(path):
        fail(f"no frame_events.json in {args.frame}: run `frame` first", code="no_snapshot")
    with open(path, encoding="utf-8") as f:
        frame = json.load(f)
    events = frame["events"]
    cores = parse_cores(args)
    if args.main_core and args.main_core not in cores:
        sys.exit(f"--main-core {args.main_core} is not one of the cores: {', '.join(cores)}")
    main_core = args.main_core or main_core_of(cores)
    overrides = {}
    for spec in args.loop_iters_shader or []:
        name, _, n = spec.rpartition("=")
        if not name or not n.isdigit():
            sys.exit(f"--loop-iters-shader wants NAME=N, got {spec!r}")
        overrides[name] = int(n)
    project = args.project or frame.get("project")
    root = args.variants or os.path.join(args.frame, "variants")
    platforms = [PLATFORM[args.api]]
    mat_keys = None
    if args.materials:
        if not project or not os.path.isdir(project):
            sys.exit(f"--materials needs the Unity project (--project), got {project}")
        rep.phase("materials")
        mat_keys, mat_skipped = project_materials.project_variants(
            project, events, [os.path.dirname(os.path.abspath(args.frame)), workspace.OUT], root,
            compile_missing=not args.no_compile)
    try:
        state = variants.run(events + ([k.as_event() for k in mat_keys] if mat_keys else []), project, root, cores,
                             platforms, args.jobs, compile_missing=not args.no_compile,
                             recompile=args.recompile, retry_failed=args.retry_failed)
    except VariantsError as e:
        fail(f"variants failed: {e}", e)
    state["loops"] = variants.loops_of(state, root, cores, args.jobs)
    rep.phase("report")
    c = frame_cost.compute(frame, events, state, args.api, cores, main_core,
                           loop_iters=args.loop_iters, loop_overrides=overrides)
    c["variants_checked"] = state["checked"]
    if mat_keys is not None:
        c["project_shaders"] = shaders.rows(mat_keys, mat_skipped, state, c, args.api, args.loop_iters, overrides)
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    c["malioc"] = ", ".join(sorted({r["malioc"] for r in recs}))
    c["frame_dir"] = os.path.abspath(args.frame)
    c["variants_dir"] = os.path.abspath(root)
    c["computed_at"] = time.time()
    out = args.out or args.frame
    os.makedirs(out, exist_ok=True)
    tables.write_frame_cost(c, out)
    run_id = cost_runs.archive(c, out)  # every run is kept: a snapshot priced again is compared with its earlier runs
    html.write_frame_report(c, os.path.join(out, "frame_report.html"), RESULT.title(c))
    results.publish(RESULT, c, out, os.path.basename(os.path.abspath(args.frame)))
    print_summary(c, state, args.api)
    for name in ("frame_report.html", "frame_cost.csv", "frame_cost.json", f"{cost_runs.COSTS}/{run_id}.json"):
        print(f"-> {os.path.abspath(os.path.join(out, name))}")
    return 0


def print_summary(c, state, api):
    cov, t = c["coverage"], c["totals"].get(c["main_core"])
    print(f"{cov['draws_priced']} of {cov['draws']} draws and {cov['events_priced'] - cov['draws_priced']} of "
          f"{cov['events'] - cov['draws']} dispatches priced, {len(state['keys'])} variants, {api}; pixels: "
          + ", ".join(f"{k} {v}" for k, v in c["pixel_methods"].items())
          + "; vertices: " + ", ".join(f"{k} {v}" for k, v in c["vertex_methods"].items()))
    if t:
        mc = c["main_core"]
        print(f"{mc}: {t['total'] / 1e6:.1f} M cycles (pixels {100 * t['fragment'] / t['total']:.0f}%, "
              f"vertices {100 * t['vertex'] / t['total']:.0f}%, compute {100 * t['compute'] / t['total']:.1f}%); "
              "by stage: " + ", ".join(f"{a['key']} {100 * a['share']:.1f}%" for a in c["groups"][mc]["stage"]))
        for r in [r for r in c["events"] if mc in r["cost"]][:10]:
            x = r["cost"][mc]
            work = (f"threads {r['threads']:>8,} x {x['cs_price']:6.2f}" if r["kind"] == "compute" else
                    f"px {r['pixels']:>10,} x {x['px_price']:6.2f}  vtx {r['vertices']:>8,} x {x['vtx_price']:5.2f}")
            print(f"  {100 * x['share']:5.1f}%  #{r['index'] + 1:<4} {r['stage']:11s} {r['object'][:38]:38s} "
                  f"{(r['shader'] or '')[:34]:34s} {work}")
        lp = c["loops"]
        if lp["events"]:
            by_n = lp["by_n"][mc]
            print(f"dynamic loops (lights, probes, ray steps) in {lp['events']} events, priced at n = {lp['n']} "
                  f"iterations" + (f", {', '.join(f'{k}={v}' for k, v in lp['overrides'].items())}" if lp["overrides"] else "")
                  + "; frame at n = " + ", ".join(
                      f"{n}: {v['total'] / 1e6:.0f} M ({100 * v['loop_share']:.0f}% in loops)" for n, v in by_n.items()))
            top = sorted({k for v in by_n.values() for k in list(v["stages"])[:3]})
            print("  stages by n: " + "; ".join(
                f"{s} " + "/".join(f"{100 * v['stages'].get(s, 0):.0f}" for v in by_n.values()) + "%" for s in top))
    if not state["checked"]:
        print("  compiled variants were NOT checked against the shaders (no editor or --no-compile): "
              "after a shader change run with the editor open")
    for m in c["missing"]:
        print(f"  no price: #{m['index'] + 1} {m['stage']} {m['shader'] or ''} {m['pass'] or ''}: {m['reason']}")
    ps = c.get("project_shaders")
    if ps:
        print(f"project shaders: {ps['materials']} materials -> {ps['variants']} variants -> {len(ps['rows'])} distinct "
              f"compiled shaders; {len(ps['unpriced'])} variants without a price, {len(ps['skipped'])} materials skipped")
        why = {}
        for x in ps["skipped"]:
            why[x["reason"].split(" (")[0]] = why.get(x["reason"].split(" (")[0], 0) + 1
        for r, n in sorted(why.items(), key=lambda kv: -kv[1]):
            print(f"  skipped {n}: {r}")


def _totals(c):
    mc = c.get("main_core")
    t = (c.get("totals") or {}).get(mc) or {}
    return {"main_core": mc, "api": c.get("api"), "total": t.get("total"), "fragment": t.get("fragment"),
            "vertex": t.get("vertex"), "compute": t.get("compute"),
            "priced": (c.get("coverage") or {}).get("draws_priced")}


RESULT = ResultKind("cost", lambda c: f"Стоимость кадра {os.path.basename(os.path.normpath(c.get('frame_dir') or '.'))}",
                    _totals, data="frame_cost.json", page="frame_report.html", own_page=True)

COST = Command(
    "cost", "cost of a frame snapshot: every event's shader variant measured with malioc, pixels x pixel price + "
            "vertices x vertex price, frame_report.html",
    [Arg("frame", help="snapshot folder with frame_events.json (from `frame`)"),
     Arg("--project", help="Unity project to compile missing variants in (default: the snapshot's; its editor must be "
                           "open)"),
     Arg("--variants", help="folder of the compiled and measured variants (default: <frame>/variants; share one "
                            "between frames of a project to compile less)"),
     cores_args(),
     Arg("--main-core", help="core shown first and printed (decision D-17; default Mali-G78 if it is among the cores, "
                             "else the first)"),
     Arg("--api", default="vulkan", choices=["vulkan", "gles"],
         help="prices of this API (default vulkan: the Android API of the target project)"),
     Arg("--no-compile", action="store_true",
         help="use only the variants already in the folder (not checked against the shaders)"),
     Arg("--materials", action="store_true",
         help="also price the shader variants of every material of the project (the \"project shaders\" tab): passes "
              "and global keywords from the project's snapshots, other shaders' passes from the open editor"),
     Arg("--recompile", action="store_true", help="compile every variant of the frame again"),
     Arg("--retry-failed", action="store_true", help="compile again the variants that failed before"),
     Arg("--loop-iters", type=int, default=LOOP_ITERS,
         help="iterations of every dynamic loop (lights and probes per pixel, ray steps) of the shaders whose longest "
              "path is N/A; the report also shows the frame at n = " + ", ".join(map(str, LOOP_NS))
              + " (default %(default)s)"),
     Arg("--loop-iters-shader", action="append", metavar="NAME=N",
         help="iterations for the shaders whose name contains NAME, e.g. Hidden/SSR=12 (repeatable)"),
     JOBS,
     Arg("--out", help="output folder (default: the snapshot folder)")],
    run_command,
    phases=lambda v: (["materials"] if v.get("materials") else []) + ["fingerprints", "compile", "measure", "loops",
                                                                         "report"],
    report=lambda v: os.path.join(v.get("out") or v["frame"], "frame_report.html"),
    ui_values=default_variants, result=RESULT)
