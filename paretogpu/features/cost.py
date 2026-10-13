"""The cost of a frame snapshot (plan items K1.4–K1.9, K1.13): `python -m paretogpu cost <snapshot>`.

The snapshot is priced by app/cost.py (every event's exact shader variant compiled and measured with malioc, dynamic
loops at n iterations, the frame priced by core/frame_cost.py): frame_cost.json / .csv, frame_report.html, and the run
is kept in <snapshot>/costs/ (store/cost_runs.py) to compare with. --materials adds the shader variants of every
material of the project ("project shaders" tab).
"""
import os

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import cost
from paretogpu.app import results
from paretogpu.core import frame_cost
from paretogpu.core import profile
from paretogpu.core.pricing import LOOP_ITERS, LOOP_NS
from paretogpu.features.common import JOBS, cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.store import cost_runs, workspace
from paretogpu.views import html, messages, tables


def run_command(args):
    if not workspace.is_snapshot(args.frame):
        fail(f"no {workspace.SNAPSHOT_FILE} in {args.frame}: run `frame` first", code="no_snapshot")
    cores = parse_cores(args)
    if args.main_core and args.main_core not in cores:
        fail(f"--main-core {args.main_core} is not one of the cores: {', '.join(cores)}", code="input")
    overrides = {}
    for spec in args.loop_iters_shader or []:
        name, _, n = spec.rpartition("=")
        if not name or not n.isdigit():
            fail(f"--loop-iters-shader wants NAME=N, got {spec!r}", code="input")
        overrides[name] = int(n)
    project = args.project or workspace.snapshot_project(args.frame)
    root = args.variants or os.path.join(args.frame, "variants")
    if args.materials and not (project and os.path.isdir(project)):
        fail(f"--materials needs the Unity project (--project), got {project}", code="input")
    try:
        c, state = cost.run(args.frame, root, cores, args.api, args.main_core, project, args.materials,
                            compile_missing=not args.no_compile, recompile=args.recompile,
                            retry_failed=args.retry_failed, jobs=args.jobs, loop_iters=args.loop_iters,
                            loop_overrides=overrides, loop_pixels=args.loop_pixels)
    except VariantsError as e:
        fail(f"variants failed: {e}", e)
    out = args.out or args.frame
    os.makedirs(out, exist_ok=True)
    tables.write_frame_cost(c, out)
    run_id = cost_runs.archive(c, out)  # every run is kept: a snapshot priced again is compared with its earlier runs
    html.write_frame_report(c, os.path.join(out, RESULT.page), RESULT.title(c))
    results.publish(RESULT, c, out, os.path.basename(os.path.abspath(args.frame)))
    print_summary(c, len(state["keys"]), args.api)
    for name in (RESULT.page, workspace.COST_CSV, RESULT.data, f"{cost_runs.COSTS}/{run_id}.json"):
        print(f"-> {os.path.abspath(os.path.join(out, name))}")
    return 0


def print_summary(c, n_variants, api):
    cov, t = c["coverage"], c["totals"].get(c["main_core"])
    print(f"{cov['draws_priced']} of {cov['draws']} draws and {cov['events_priced'] - cov['draws_priced']} of "
          f"{cov['events'] - cov['draws']} dispatches priced, {n_variants} variants, {api}; pixels: "
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
            print(f"dynamic loops (lights, probes, ray steps) in {lp['events']} events: repeats from the shader "
                  f"parameters in {lp.get('bounded', 0)}, from the capture in {lp.get('measured', 0)}, the others at "
                  f"n = {lp['n']} iterations" + (f", {', '.join(f'{k}={v}' for k, v in lp['overrides'].items())}" if lp["overrides"] else ""))
            if lp.get("unknown", 1):  # the range of n only while some loop's repeats are a guess
                print("  frame at n = " + ", ".join(
                    f"{n}: {v['total'] / 1e6:.0f} M ({100 * v['loop_share']:.0f}% in loops)" for n, v in by_n.items()))
                top = sorted({k for v in by_n.values() for k in list(v["stages"])[:3]})
                print("  stages by n: " + "; ".join(
                    f"{s} " + "/".join(f"{100 * v['stages'].get(s, 0):.0f}" for v in by_n.values()) + "%" for s in top))
    m = c.get("memory")
    if m and t:
        b, r = m["totals"]["bytes"], m["totals"]["roofline"].get(c["main_core"])
        print(f"memory: {len(m['passes'])} passes, {b['total'] / 2 ** 20:.1f} MB (load {b['load'] / 2 ** 20:.1f}, "
              f"store {b['store'] / 2 ** 20:.1f}, textures {b['textures'] / 2 ** 20:.1f})" + (
                  f"; {m['assumptions']['devices'][c['main_core']]['device']}: shaders {r['alu_ms']:.2f} ms, "
                  f"memory {r['mem_ms']:.2f} ms, {r['memory_passes']} passes memory bound "
                  f"({100 * r['memory_cycles_share']:.1f}% of the cycles)" if r else ""))
    prof = c.get("profile") or {}
    print(profile.summary(prof))
    for w in prof.get("warnings") or []:
        print(f"  WARNING: {messages.text(w)}")
    if not c["variants_checked"]:
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


TOTALS = ("main_core", "api", "total", "fragment", "vertex", "compute", "priced")


def _totals(c):
    """What the list of results says about a cost: its part of core/frame_cost.summary."""
    s = frame_cost.summary(c)
    return {k: s[k] for k in TOTALS}


RESULT = ResultKind("cost", lambda c: f"Стоимость кадра {os.path.basename(os.path.normpath(c.get('frame_dir') or '.'))}",
                    _totals, data=workspace.COST_FILE, page=workspace.REPORT_PAGE, own_page=True)

COST = Command(
    "cost", "cost of a frame snapshot: every event's shader variant measured with malioc, pixels x pixel price + "
            "vertices x vertex price, frame_report.html",
    [Arg("frame", help="snapshot folder with frame_events.json (from `frame`)", label="Снимок"),
     Arg("--project", help="Unity project to compile missing variants in (default: the snapshot's; its editor must be "
                           "open)", label="Проект"),
     Arg("--variants", help="folder of the compiled and measured variants (default: <frame>/variants; share one "
                            "between frames of a project to compile less)", label="Папка скомпилированных вариантов"),
     cores_args(),
     Arg("--main-core", help="core shown first and printed (decision D-17; default Mali-G78 if it is among the cores, "
                             "else the first)", label="Основное ядро"),
     Arg("--api", default="vulkan", choices=["vulkan", "gles"],
         help="prices of this API (default vulkan: the Android API of the target project)", label="API цен"),
     Arg("--no-compile", action="store_true",
         help="use only the variants already in the folder (not checked against the shaders)", label="Не компилировать"),
     Arg("--materials", action="store_true", label="Все материалы проекта",
         help="also price the shader variants of every material of the project (the \"project shaders\" tab): passes "
              "and global keywords from the project's snapshots, other shaders' passes from the open editor"),
     Arg("--recompile", action="store_true", help="compile every variant of the frame again",
         label="Скомпилировать всё заново"),
     Arg("--retry-failed", action="store_true", help="compile again the variants that failed before",
         label="Повторить упавшие варианты"),
     Arg("--loop-iters", type=int, default=LOOP_ITERS,
         help="iterations of every dynamic loop (lights and probes per pixel, ray steps) of the shaders whose longest "
              "path is N/A; the report also shows the frame at n = " + ", ".join(map(str, LOOP_NS))
              + " (default %(default)s)", label="Итераций динамических циклов"),
     Arg("--loop-pixels", type=int, default=16, label="Пикселей на draw для подсчёта циклов",
         help="pixels of every draw with a loop of unknown trip count (lights / probes per pixel) debugged in the "
              "snapshot's RenderDoc capture to count how many times its loops ran (0: none; default %(default)s)"),
     Arg("--loop-iters-shader", action="append", metavar="NAME=N",
         help="iterations for the shaders whose name contains NAME, e.g. Hidden/SSR=12 (repeatable)",
         label="Итерации по шейдерам (NAME=N через «;»)"),
     JOBS,
     Arg("--out", help="output folder (default: the snapshot folder)", label="Папка результата")],
    run_command, title="Цена кадра",
    phases=lambda v: (["materials"] if v.get("materials") else []) + ["fingerprints", "compile", "measure", "loops",
                                                                         "bounds", "rd_loops", "rd_memory",
                                                                         "report"],
    report=lambda v: os.path.join(v.get("out") or v["frame"], RESULT.page),
    ui_values=default_variants, result=RESULT)
