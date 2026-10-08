"""ParetoGPU command line.

  python -m paretogpu measure <folder> [--cores preset:mobile|Mali-G78,...] [--api gles] [--out DIR]
                              [--fp16-threshold 25] [--no-report] [--jobs N]
  python -m paretogpu report <folder with measurements.jsonl> [--out DIR]
  python -m paretogpu export --project <Unity project> <shader> [...] [--out DIR] [--platforms gles3,vulkan]
                             [--mode auto|editor|batch] [--measure] [--cores ...]
  python -m paretogpu cost <frame folder> [--project <Unity>] [--cores preset:mobile]
                           [--main-core Mali-G78] [--api vulkan|gles] [--variants DIR] [--no-compile]
                           [--recompile] [--retry-failed] [--loop-iters 2] [--loop-iters-shader NAME=N ...] [--materials]
  python -m paretogpu compare <A> <B> [--out DIR]   A, B: snapshot folder (its latest cost), frame_cost.json or
                                                   <snapshot>/costs/<time>.json -> compare.html, compare.json
  python -m paretogpu matcompare --project <Unity> <A.mat> <B.mat> [--cores preset:mobile] [--api vulkan|gles]
                                 [--variants DIR] [--out DIR] [--no-compile] [--recompile]
                                 two materials: their variants (keywords, passes) per pixel / vertex at loop n = 0..8
  python -m paretogpu matshader --project <Unity> <material.mat> [--cores ...] [--ablate-cores Mali-G78] [--n 2]
                                one material: its variants and what every line of their code costs (ablation)
  python -m paretogpu hotspots <snapshot folder> [--top 10] [--core Mali-G78]
                               the heaviest shaders of a priced snapshot: their costliest parts and why -> hotspots.html
  python -m paretogpu ui [--port 8765] [--no-window]   local web UI: the function cost site, runs with progress, reports
"""
import argparse
import os
import sys

from paretogpu.adapters import malioc as mali
from paretogpu.app import measure
from paretogpu.model import cores as core_presets
from paretogpu.model.cores import MAIN_CORE, main_core as main_core_of
from paretogpu.store import workspace
from paretogpu.store.workspace import OUT
from paretogpu.views.reporter import CONSOLE as rep


def parse_cores(args):
    try:
        return mali.parse_cores(args.core or args.cores)
    except (ValueError, mali.MaliocError) as e:
        fail(str(e), e)


def fail(message, e=None, code=None):
    """Stop the command: the message for the console, the error code (model/errors.py) for the UI."""
    rep.error(code or getattr(e, "code", "error"))
    sys.exit(message)


def positive_int(v):
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {v}")
    return n


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


def cmd_measure(args):
    if not os.path.isdir(args.folder):
        sys.exit(f"not a folder: {args.folder}")
    cores = parse_cores(args)
    failed = measure_folder(args.folder, cores, args.api, args.out, args.jobs)
    if failed is None:
        sys.exit(f"no .vert / .frag / .vert.spv / .frag.spv files in {args.folder}")
    if not args.no_report:
        make_report(args.out or args.folder, None, args)
    return 1 if failed else 0


def cmd_report(args):
    if not os.path.exists(os.path.join(args.folder, "measurements.jsonl")):
        sys.exit(f"no measurements.jsonl in {args.folder}: run `measure` first")
    make_report(args.folder, args.out, args)
    return 0


def cmd_frame(args):
    from collections import Counter
    from paretogpu.adapters.renderdoc import RenderDocError
    from paretogpu.features import frame as snapshot
    out = args.out or workspace.new_snapshot_dir(args.project, args.suffix)
    try:
        meta, events = snapshot.run(args.project, out, args.timeout, args.max_events)
    except (snapshot.SnapshotError, RenderDocError) as e:
        fail(f"snapshot failed: {e}", e)
    draws = [e for e in events if e["kind"] == "draw"]
    print(f"Unity {meta['unity']} ({meta['graphics_api']}, quality {meta['quality']}, "
          f"{'play' if meta['play_mode'] else 'edit'} mode): {len(events)} events in {meta['seconds']} s")
    stages = Counter(e["stage"] for e in events)
    print("stages: " + ", ".join(f"{s} {n}" for s, n in stages.most_common()))
    variants = {(e["shader"], e["pass"], tuple(e["keywords"])) for e in draws}
    print(f"{len(draws)} draws, {len(variants)} shader variants, "
          f"{sum(e['vertices'] for e in draws)} vertices, {len({e['rt']['name'] for e in events})} render targets")
    methods = Counter(e["pixel_method"] for e in draws)
    stage_px = Counter()
    for e in draws:
        stage_px[e["stage"]] += e["pixel_count"] or 0
    print("pixels by stage: " + ", ".join(f"{s} {n:,}" for s, n in stage_px.most_common())
          + "  (" + ", ".join(f"{m} {n}" for m, n in methods.most_common()) + ")")
    if meta.get("renderdoc"):
        r = meta["renderdoc"]
        print(f"renderdoc: {r['matched']} events matched, {r['missing']} without calls -> {r['capture']}")
        if r.get("mismatched"):
            print(f"  WARNING: {r['mismatched']} draws whose RenderDoc calls do not add up to the Frame Debugger index "
                  "count (ev['rd']['count_mismatch']): the call order went astray, their counters may be another draw's")
        lost = {"ps_invocations", "vs_invocations", "cs_invocations"} - set(r.get("counters") or [])
        if lost:
            print(f"  WARNING: the capture has no {', '.join(sorted(lost))}: those counts fall back to other sources")
    print(f"-> {os.path.join(out, 'frame_events.json')}")
    return 0


def cmd_cost(args):
    import json
    import time
    from paretogpu.adapters.unity.variants import VariantsError
    from paretogpu.app import variants
    from paretogpu.core import frame_cost
    from paretogpu.core import materials as shaders
    from paretogpu.store import cost_runs
    from paretogpu.views import html as frame_report
    from paretogpu.views import tables
    path = os.path.join(args.frame, "frame_events.json")
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
    platforms = ["vulkan"] if args.vulkan_only else ["gles3", "vulkan"]
    mat_keys = None
    if args.materials:
        if not project or not os.path.isdir(project):
            sys.exit(f"--materials needs the Unity project (--project), got {project}")
        rep.phase("materials")
        mat_keys, mat_skipped = project_variants(project, events, args, root)
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
    frame_report.write_frame_report(c, os.path.join(out, "frame_report.html"),
                       f"Стоимость кадра {os.path.basename(os.path.abspath(args.frame))}")

    cov, t = c["coverage"], c["totals"].get(c["main_core"])
    print(f"{cov['draws_priced']} of {cov['draws']} draws and {cov['events_priced'] - cov['draws_priced']} of "
          f"{cov['events'] - cov['draws']} dispatches priced, {len(state['keys'])} variants, {args.api}; pixels: "
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
    for name in ("frame_report.html", "frame_cost.csv", "frame_cost.json", f"{cost_runs.COSTS}/{run_id}.json"):
        print(f"-> {os.path.abspath(os.path.join(out, name))}")
    return 0


def cmd_compare(args):
    from paretogpu.core import compare
    from paretogpu.store import cost_runs
    from paretogpu.views import html
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


def cmd_matcompare(args):
    from paretogpu.adapters.unity.variants import VariantsError
    from paretogpu.features import matcompare
    cores = parse_cores(args)
    project = os.path.abspath(args.project)
    root = args.variants or workspace.variants_dir(project)
    out = args.out or workspace.new_result_dir("matcompare")
    try:
        res = matcompare.run(project, args.a, args.b, root, cores,
                             ["vulkan"] if args.vulkan_only else ["gles3", "vulkan"], args.api, args.jobs,
                             compile_missing=not args.no_compile, recompile=args.recompile, snapshot_folders=[OUT])
    except (ValueError, VariantsError) as e:
        fail(f"matcompare: {e}", e)
    for w in res["warnings"]:
        print(f"  ! {w}")
    mc = res["main_core"]
    for ia, ib in res["pairs"]:
        pa = res["a"]["passes"][ia] if ia is not None else None
        pb = res["b"]["passes"][ib] if ib is not None else None
        fmt = lambda p, k: "/".join(f"{p['prices'][mc][k][str(n)]:.1f}" for n in res["ns"])             if p and mc in p["prices"] else "-"
        print(f"{mc} {(pa or pb)['pass']}: pixel A {fmt(pa, 'px')}  B {fmt(pb, 'px')}; vertex A {fmt(pa, 'vtx')}  "
              f"B {fmt(pb, 'vtx')}  (cycles at n = {'/'.join(map(str, res['ns']))})")
    print(f"-> {os.path.abspath(matcompare.write(res, out))}")
    return 0


def cmd_matshader(args):
    from paretogpu.adapters.unity.variants import VariantsError
    from paretogpu.features import matshader
    cores = parse_cores(args)
    ablate = [c.strip() for c in args.ablate_cores.split(",")] if args.ablate_cores else [main_core_of(cores)]
    bad = [c for c in ablate if c not in cores]
    if bad:
        sys.exit(f"--ablate-cores {', '.join(bad)}: not among the cores ({', '.join(cores)})")
    project = os.path.abspath(args.project)
    root = args.variants or workspace.variants_dir(project)
    out = args.out or workspace.new_result_dir("matshader")
    try:
        res = matshader.run(project, args.material, root, cores, ["gles3", "vulkan"], args.api, args.jobs,
                            compile_missing=not args.no_compile, recompile=args.recompile, snapshot_folders=[OUT],
                            ablate_cores=ablate, n=args.n)
    except (ValueError, VariantsError) as e:
        fail(f"matshader: {e}", e)
    for w in res["warnings"]:
        print(f"  ! {w}")
    for p in res["m"]["passes"]:
        for stage, a in (p.get("ablation") or {}).items():
            if "error" in a:
                print(f"{p['pass']} {stage}: {a['error']}")
                continue
            for core, x in a["by_core"].items():
                if "error" in x["base"]:
                    print(f"{p['pass']} {stage} {core}: {x['base']['error']}")
                    continue
                parts = sorted((sid for sid, st in a["statements"].items() if st["parent"] is None
                                and "error" not in x["stmts"].get(sid, {"error": 1})),
                               key=lambda sid: -x["stmts"][sid]["price"])
                print(f"{p['pass']} {stage} {core}: {x['base']['price']} cycles (GLES, n = {a['n']}), "
                      f"{len(a['statements'])} statements; costliest parts:")
                for sid in parts[:5]:
                    st = a["statements"][sid]
                    print(f"  -{x['stmts'][sid]['price']:6.2f}  line {st['line'] + 1}: {st['text'][:80]}")
    print(f"-> {os.path.abspath(matshader.write(res, out))}")
    return 0


def cmd_hotspots(args):
    from paretogpu.features import hotspots
    try:
        res = hotspots.run(args.frame, args.top, args.core, args.jobs)
    except ValueError as e:
        fail(f"hotspots: {e}", e)
    for it in res["shaders"]:
        print(f"{100 * (it['share'] or 0):5.1f}%  {it['variant']}")
        for stage, a in it["ablation"].items():
            if "error" in a:
                print(f"        {stage}: {a['error']}")
                continue
            x = a["by_core"][res["core"]]
            if "error" in x["base"]:
                print(f"        {stage}: {x['base']['error']}")
                continue
            roots = sorted((sid for sid, st in a["statements"].items() if st["parent"] is None
                            and "error" not in x["stmts"].get(sid, {"error": 1})),
                           key=lambda sid: (-x["stmts"][sid]["price"], -x["stmts"][sid]["incl"].get("arith", 0)))
            print(f"        {stage} {x['base']['price']} cycles (GLES, n = {a['n']}): "
                  + "; ".join(f"line {a['statements'][sid]['line'] + 1} -{x['stmts'][sid]['price']:.2f} "
                              f"({a['statements'][sid]['explain']['text']})" for sid in roots[:3]))
            if a.get("vertex_candidates"):
                print(f"        to the vertex shader: {len(a['vertex_candidates'])} candidates")
    print(f"-> {os.path.abspath(hotspots.write(res, args.out or args.frame))}")
    return 0


def project_variants(project, events, args, root):
    """Variants of the project's materials (core/materials.py): passes and global keywords from every snapshot of
    the project (this one, its sibling folders and paretogpu/out), the shaders' passes from the open editor."""
    from paretogpu.adapters.unity import assets as materials
    from paretogpu.adapters.unity.variants import VariantsError
    from paretogpu.app import variants
    from paretogpu.core import materials as shaders
    from paretogpu.store import workspace
    mats = materials.scan(project)
    folders = [os.path.dirname(os.path.abspath(args.frame)), OUT]
    snaps = [events] + workspace.snapshots_of(project, folders)
    info = shaders.snapshot_info(snaps)
    names = shaders.material_shaders(mats)
    editor = None
    if names and not args.no_compile:
        from paretogpu.adapters.unity import cli as unity
        if unity.editor_ready(project, allow_play=True):
            try:
                editor = variants.shader_query(project, "Passes", names, root)
            except VariantsError as e:
                print(f"passes of the materials' shaders: {e}")
    if editor is None:
        print("passes of the materials' shaders are taken from the snapshots only "
              + ("(--no-compile)" if args.no_compile else "(the editor does not answer Unity CLI)"))
    keys, skipped = shaders.plan(mats, info, editor)
    print(f"materials: {len(mats)} in {project}, {len(snaps)} snapshots -> {len(keys)} variants")
    return keys, skipped


def cmd_export(args):
    from paretogpu.adapters.unity import export
    from paretogpu.adapters.unity.cli import UnityError
    cores = parse_cores(args) if args.measure else None
    out = args.out or workspace.export_dir(args.project)
    try:
        rep.phase("unity_export")
        res = export.export(args.project, args.shaders, out, args.platforms.split(","), args.mode, args.timeout)
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


def frame_cost_defaults():
    from paretogpu.core import pricing
    return pricing.LOOP_ITERS, pricing.LOOP_NS


def add_report_args(p):
    p.add_argument("--fp16-threshold", type=float, default=25, help="low_fp16 flag below this percent (default 25)")


def add_core_args(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--cores", default=MAIN_CORE,
                   help="comma separated cores and/or presets; preset:mobile = " + ",".join(core_presets.PRESETS["mobile"])
                        + " (default Mali-G78)")
    g.add_argument("--core", help="one core (same as --cores <core>)")
    p.add_argument("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)")


def cmd_ui(args):
    from paretogpu.ui import server
    return server.serve(args.port, open_window=not args.no_window, stay=args.stay)


def build_parser():
    ap = argparse.ArgumentParser(prog="paretogpu")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="measure every .vert / .frag (GLSL) and .vert.spv / .frag.spv "
                                       "(SPIR-V, Vulkan only) in a folder with malioc, then write the report")
    m.add_argument("folder")
    add_core_args(m)
    m.add_argument("--api", default="gles", choices=["gles", "vulkan"],
                   help="API for GLSL files (SPIR-V is always vulkan)")
    m.add_argument("--out", help="output folder (default: the measured folder)")
    m.add_argument("--no-report", action="store_true", help="skip report.csv")
    add_report_args(m)
    m.set_defaults(func=cmd_measure)

    r = sub.add_parser("report", help="report.csv (variants ranked on the main core) from measurements.jsonl")
    r.add_argument("folder")
    r.add_argument("--out", help="output folder (default: the folder)")
    add_report_args(r)
    r.set_defaults(func=cmd_report)

    e = sub.add_parser("export", help="compile shaders of a Unity project into per-variant GLSL / SPIR-V files "
                                      "(in the open editor via Unity CLI, else in batchmode)")
    e.add_argument("shaders", nargs="+", help="asset path (Assets/.../X.shadergraph), folder under Assets/ "
                                              "or shader name (Universal Render Pipeline/Lit)")
    e.add_argument("--project", required=True, help="Unity project folder")
    e.add_argument("--out", help="output folder (default: paretogpu/corpus/real/<project name>, not in git)")
    e.add_argument("--platforms", default="gles3,vulkan", help="comma separated: gles3, vulkan")
    e.add_argument("--mode", default="auto", choices=["auto", "editor", "batch"],
                   help="auto: the open editor if it answers Unity CLI, else batchmode")
    e.add_argument("--timeout", type=int, default=1800, help="seconds")
    e.add_argument("--measure", action="store_true", help="measure the output folder with malioc and write the report")
    add_core_args(e)
    add_report_args(e)
    e.set_defaults(func=cmd_export)

    f = sub.add_parser("frame", help="snapshot the frame of the open Unity editor: RenderDoc (loaded in the editor: "
                                     "Game tab -> Load RenderDoc) for the pixels and vertices, the Frame Debugger for "
                                     "the shader variant of every event")
    f.add_argument("--project", required=True, help="Unity project folder (its editor must be open)")
    f.add_argument("--out", help="output folder (default: paretogpu/out/frame_<project>_<time>, not in git)")
    f.add_argument("--suffix", help="appended to the snapshot folder name: frame_<project>_<time>_<suffix>")
    f.add_argument("--timeout", type=int, default=1800, help="seconds")
    f.add_argument("--max-events", type=int, default=0, help="stop after N events (0 = all)")
    f.set_defaults(func=cmd_frame)

    c = sub.add_parser("cost", help="cost of a frame snapshot: every event's shader variant measured with malioc, "
                                    "pixels x pixel price + vertices x vertex price, frame_report.html")
    c.add_argument("frame", help="snapshot folder with frame_events.json (from `frame`)")
    c.add_argument("--project", help="Unity project to compile missing variants in (default: the snapshot's; "
                                     "its editor must be open)")
    c.add_argument("--variants", help="folder of the compiled and measured variants (default: <frame>/variants; "
                                      "share one between frames of a project to compile less)")
    cg = c.add_mutually_exclusive_group()
    cg.add_argument("--cores", default="preset:mobile",
                   help="comma separated cores and/or presets (default preset:mobile)")
    cg.add_argument("--core", help="one core (same as --cores <core>)")
    c.add_argument("--main-core",
                   help="core shown first and printed (decision D-17; default Mali-G78 if it is among the cores, else the first)")
    c.add_argument("--api", default="vulkan", choices=["vulkan", "gles"],
                   help="prices of this API (default vulkan: the Android API of the target project)")
    c.add_argument("--vulkan-only", action="store_true", help="compile only Vulkan variants (no GLES3)")
    c.add_argument("--no-compile", action="store_true",
                   help="use only the variants already in the folder (not checked against the shaders)")
    c.add_argument("--materials", action="store_true",
                   help="also price the shader variants of every material of the project (the \"project shaders\" "
                        "tab): passes and global keywords from the project's snapshots, other shaders' passes from "
                        "the open editor")
    c.add_argument("--recompile", action="store_true", help="compile every variant of the frame again")
    c.add_argument("--retry-failed", action="store_true", help="compile again the variants that failed before")
    c.add_argument("--loop-iters", type=int, default=frame_cost_defaults()[0],
                   help="iterations of every dynamic loop (lights and probes per pixel, ray steps) of the shaders "
                        "whose longest path is N/A; the report also shows the frame at n = "
                        + ", ".join(map(str, frame_cost_defaults()[1])) + " (default %(default)s)")
    c.add_argument("--loop-iters-shader", action="append", metavar="NAME=N",
                   help="iterations for the shaders whose name contains NAME, e.g. Hidden/SSR=12 (repeatable)")
    c.add_argument("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)")
    c.add_argument("--out", help="output folder (default: the snapshot folder)")
    c.set_defaults(func=cmd_cost)

    k = sub.add_parser("compare", help="compare two cost runs: the frame, its stages and shaders, every change "
                                       "split into price (the shaders), work (pixels, vertices) and mix (variants)")
    k.add_argument("a", help="before: snapshot folder (its latest cost), frame_cost.json or <snapshot>/costs/<time>.json")
    k.add_argument("b", help="after: the same kinds")
    k.add_argument("--out", help="folder for compare.html and compare.json (default: B's folder)")
    k.set_defaults(func=cmd_compare)

    m = sub.add_parser("matcompare", help="two materials of a Unity project against each other: their shader variants "
                                          "(material and pipeline keywords, color passes) per pixel and vertex, "
                                          "dynamic loops at n = 0..8; needs the open editor to compile")
    m.add_argument("a", help="material A: .mat path, relative to the project or absolute")
    m.add_argument("b", help="material B")
    m.add_argument("--project", required=True, help="Unity project folder (its editor must be open to compile)")
    m.add_argument("--variants", help="folder of the compiled variants (default: paretogpu/out/variants_<project>, "
                                      "the one the frames of the project share)")
    mg = m.add_mutually_exclusive_group()
    mg.add_argument("--cores", default="preset:mobile", help="comma separated cores and/or presets (default preset:mobile)")
    mg.add_argument("--core", help="one core (same as --cores <core>)")
    m.add_argument("--api", default="vulkan", choices=["vulkan", "gles"], help="prices of this API (default vulkan)")
    m.add_argument("--vulkan-only", action="store_true", help="compile only Vulkan variants (no GLES3)")
    m.add_argument("--no-compile", action="store_true", help="use only the variants already in the folder")
    m.add_argument("--recompile", action="store_true", help="compile the materials' variants again")
    m.add_argument("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)")
    m.add_argument("--out", help="output folder (default: paretogpu/out/_matcompare/<time>)")
    m.set_defaults(func=cmd_matcompare)

    s_ = sub.add_parser("matshader", help="one material of a Unity project: its shader variants (material and pipeline "
                                           "keywords, color passes), their prices and what every line of their code "
                                           "costs (ablation, plan A3.2); needs the open editor to compile")
    s_.add_argument("material", help=".mat path, relative to the project or absolute")
    s_.add_argument("--project", required=True, help="Unity project folder (its editor must be open to compile)")
    s_.add_argument("--variants", help="folder of the compiled variants (default: paretogpu/out/variants_<project>)")
    sg = s_.add_mutually_exclusive_group()
    sg.add_argument("--cores", default="preset:mobile", help="comma separated cores and/or presets (default preset:mobile)")
    sg.add_argument("--core", help="one core (same as --cores <core>)")
    s_.add_argument("--ablate-cores", help="cores to ablate on, comma separated (default Mali-G78): every statement "
                                           "is one malioc run per core")
    s_.add_argument("--n", type=int, default=frame_cost_defaults()[0],
                    help="iterations of the dynamic loops in the ablation (default %(default)s)")
    s_.add_argument("--api", default="vulkan", choices=["vulkan", "gles"], help="main API of the prices (default vulkan)")
    s_.add_argument("--no-compile", action="store_true", help="use only the variants already in the folder")
    s_.add_argument("--recompile", action="store_true", help="compile the material's variants again")
    s_.add_argument("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)")
    s_.add_argument("--out", help="output folder (default: paretogpu/out/_matshader/<time>)")
    s_.set_defaults(func=cmd_matshader)

    h = sub.add_parser("hotspots", help="why the heaviest shaders of a priced snapshot are heavy: every line of their "
                                        "GLES variant ablated (plan A3.5), the costliest parts and what they do, the "
                                        "candidates for moving to the vertex shader -> hotspots.html")
    h.add_argument("frame", help="snapshot folder with frame_cost.json (after `cost`)")
    h.add_argument("--top", type=positive_int, default=10, help="how many of the costliest variants (default %(default)s)")
    h.add_argument("--core", help="core to ablate on (default: the snapshot's main core)")
    h.add_argument("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)")
    h.add_argument("--out", help="output folder (default: the snapshot folder)")
    h.set_defaults(func=cmd_hotspots)

    u = sub.add_parser("ui", help="local web UI (127.0.0.1): the function cost site, the commands with their "
                                  "progress, the reports of the snapshots")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-window", action="store_true", help="do not open the window (Edge app mode or the browser)")
    u.add_argument("--stay", action="store_true", help="keep running after the last window is closed")
    u.set_defaults(func=cmd_ui)
    return ap


def main(argv=None):
    # names of shaders and objects may have characters the console code page cannot print (cp1251 into a pipe)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
