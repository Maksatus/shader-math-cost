import os

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import cost
from paretogpu.app.frame_draws import LoopCountError
from paretogpu.app import results
from paretogpu.app.variants import PipelineMismatch
from paretogpu.core import frame_cost
from paretogpu.core import profile
from paretogpu.features.common import cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.store import cost_runs, workspace
from paretogpu.views import html, messages, tables


def run_command(args):
    if not workspace.is_snapshot(args.frame):
        fail(f"no {workspace.SNAPSHOT_FILE} in {args.frame}: run `frame` first", code="no_snapshot")
    cores = parse_cores(args)
    project = args.project or workspace.snapshot_project(args.frame)
    root = args.variants or (workspace.variants_dir(project) if project else os.path.join(args.frame, "variants"))
    if args.materials and not (project and os.path.isdir(project)):
        fail(f"--materials needs the Unity project (--project), got {project}", code="input")
    try:
        c, state = cost.run(args.frame, root, cores, project, args.materials,
                            compile_missing=not args.no_compile)
    except VariantsError as e:
        fail(f"variants failed: {e}", e)
    except PipelineMismatch as e:
        fail(str(e), e)
    except LoopCountError as e:
        fail(f"loop trip counts failed: {e}", e)
    except ValueError as e:
        fail(str(e), e, "input")
    out = args.out or args.frame
    os.makedirs(out, exist_ok=True)
    tables.write_frame_cost(c, out)
    run_id = cost_runs.archive(c, out)
    html.write_frame_report(c, os.path.join(out, RESULT.page), RESULT.title(c),
                           os.path.join(args.frame, workspace.THUMB))
    results.publish(RESULT, c, out, os.path.basename(os.path.abspath(args.frame)))
    print_summary(c, len(state["keys"]))
    for name in (RESULT.page, workspace.COST_CSV, RESULT.data, f"{cost_runs.COSTS}/{run_id}.json"):
        print(f"-> {os.path.abspath(os.path.join(out, name))}")
    return 0


def print_summary(c, n_variants):
    cov, t = c["coverage"], c["totals"].get(c["main_core"])
    print(f"{cov['draws_priced']} of {cov['draws']} draws and {cov['events_priced'] - cov['draws_priced']} of "
          f"{cov['events'] - cov['draws']} dispatches priced, {n_variants} variants; pixels: "
          + ", ".join(f"{k} {v}" for k, v in c["pixel_methods"].items())
          + "; vertices: " + ", ".join(f"{k} {v}" for k, v in c["vertex_methods"].items()))
    if t and t["total"]:
        mc = c["main_core"]
        print(f"{mc}: {t['total'] / 1e6:.1f} M cycles (pixels {100 * t['fragment'] / t['total']:.0f}%, "
              f"vertices {100 * t['vertex'] / t['total']:.0f}%, compute {100 * t['compute'] / t['total']:.1f}%); "
              "by stage: " + ", ".join(f"{a['key']} {100 * a['share']:.1f}%" for a in c["groups"][mc]["stage"]))
        for r in [r for r in c["events"] if mc in r["cost"]][:10]:
            x = r["cost"][mc]
            work = (f"threads {r['threads']:>8,} x {x['cs_price']:6.2f}" if r["kind"] == "compute" else
                    f"threads {r['pixels']:>8,} x {x['px_price']:6.2f}" if r["kind"] == "tile" else
                    f"px {r['pixels']:>10,} x {x['px_price']:6.2f}  vtx {r['vertices']:>8,} x {x['vtx_price']:5.2f}")
            print(f"  {100 * x['share']:5.1f}%  #{r['index'] + 1:<4} {r['stage']:11s} {r['object'][:38]:38s} "
                  f"{(r['shader'] or '')[:34]:34s} {work}")
        lp = c["loops"]
        if lp["events"]:
            print(f"dynamic loops (lights, probes, ray steps) in {lp['events']} events: repeats from the shader "
                  f"parameters in {lp.get('bounded', 0)}, from the capture in {lp.get('measured', 0)}, the others at "
                  f"n = {lp['n']} iterations")
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


TOTALS = ("main_core", "total", "fragment", "vertex", "compute", "priced", "code_version")


def _totals(c):
    s = frame_cost.summary(c)
    return {k: s[k] for k in TOTALS}


RESULT = ResultKind("cost", lambda c: f"Стоимость frame {os.path.basename(os.path.normpath(c.get('frame_dir') or '.'))}",
                    _totals, data=workspace.COST_FILE, page=workspace.REPORT_PAGE, own_page=True)

COST = Command(
    "cost", "cost of a frame snapshot: every event's shader variant measured with malioc, pixels x pixel price + "
            "vertices x vertex price, frame_report.html",
    [Arg("frame", help="snapshot folder with frame_events.json (from `frame`)", label="Snapshot"),
     Arg("--project", help="Unity project to compile missing variants in (default: the snapshot's; its editor must be "
                           "open)", label="Проект"),
     Arg("--variants", help="folder of the compiled and measured variants (default: the project's, "
                            "paretogpu/out/variants_<project>: a variant is compiled once for every frame)",
         label="Папка скомпилированных вариантов"),
     cores_args(),
     Arg("--no-compile", action="store_true",
         help="use only the variants already in the folder (not checked against the shaders)", label="Не компилировать"),
     Arg("--materials", action="store_true", label="Все материалы проекта",
         help="also price the shader variants of every material of the project (the \"project shaders\" tab): passes "
              "and global keywords from the project's snapshots, other shaders' passes from the open editor"),
     Arg("--out", help="output folder (default: the snapshot folder)", label="Папка результата")],
    run_command, title="Цена frame",
    phases=lambda v: (["materials"] if v.get("materials") else []) + ["fingerprints", "compile", "measure", "loops",
                                                                         "bounds", "quads", "rd_memory",
                                                                         "report"],
    report=lambda v: os.path.join(v.get("out") or v["frame"], RESULT.page),
    ui_values=default_variants, result=RESULT)
