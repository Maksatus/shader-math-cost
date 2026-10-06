"""shaderopt command line.

  python -m shaderopt measure <folder> [--cores preset:mobile|Mali-G78,...] [--api gles] [--out DIR]
                              [--top 20] [--fp16-threshold 25] [--no-report] [--jobs N]
  python -m shaderopt report <folder with measurements.jsonl> [--out DIR] [--top 20]
  python -m shaderopt export --project <Unity project> <shader> [...] [--out DIR] [--platforms gles3,vulkan]
                             [--mode auto|editor|batch] [--measure] [--cores ...]
  python -m shaderopt cost <frame folder> [--project <Unity>] [--frustum <folder>] [--cores preset:mobile]
                           [--main-core Mali-G78] [--api vulkan|gles] [--variants DIR] [--no-compile]
"""
import argparse
import os
import sys

from shaderopt.profile import cores as core_presets
from shaderopt.profile import measure, report

REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus", "real")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def parse_cores(args):
    try:
        return core_presets.parse(args.core or args.cores)
    except ValueError as e:
        sys.exit(str(e))


def make_report(folder, out, args):
    rows, cores, _ = report.run(folder, out, args.top, args.fp16_threshold)
    out = out or folder
    for stage in ("fragment", "vertex"):
        top = [r for r in rows if r["stage"] == stage][:3]
        if top:
            print(f"top {stage}: " + "; ".join(f"{r['shader']} {r['pass']} {' '.join(r['keywords']) or '-'} "
                                               f"[{r['api']}] {r['worst']['cycles']} cycles on {r['worst']['core']}" for r in top))
    print(f"-> {os.path.abspath(os.path.join(out, 'report.html'))}")
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
    import time
    from collections import Counter
    from shaderopt.frame import snapshot
    name = os.path.basename(os.path.abspath(args.project))
    out = args.out or os.path.join(OUT, f"frame_{name}_{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        meta, events = snapshot.run(args.project, out, args.timeout, args.max_events, renderdoc=args.renderdoc)
    except (snapshot.SnapshotError, snapshot.rdoc.RenderDocError) as e:
        sys.exit(f"snapshot failed: {e}")
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
    print(f"-> {os.path.join(out, 'frame_events.json')}")
    return 0


def cmd_frustum(args):
    import time
    from shaderopt.frame import frustum
    name = os.path.basename(os.path.abspath(args.project))
    out = args.out or os.path.join(OUT, f"frustum_{name}_{time.strftime('%Y%m%d_%H%M%S')}")
    try:
        d = frustum.run(args.project, out, args.camera)
    except frustum.FrustumError as e:
        sys.exit(f"frustum failed: {e}")
    items, screen = d["items"], d["width"] * d["height"]
    print(f"camera {d['camera']} {d['width']}x{d['height']} ({'play' if d['play_mode'] else 'edit'} mode): "
          f"{len(items)} renderer x material in the frustum, {sum(i['rendered'] for i in items)} drawn by Unity")
    for label, sel in (("opaque", [i for i in items if i["opaque"]]), ("transparent", [i for i in items if not i["opaque"]])):
        print(f"{label:12s} {len(sel):4d}  raster {sum(i['raster_px'] for i in sel) / screen:5.2f} screens, "
              f"visible {sum(i['visible_px'] for i in sel) / screen:5.2f} screens")
    print("top raster: " + "; ".join(f"{i['mesh'] or i['renderer'].split('/')[-1]} ({i['shader']}) {i['raster_px']:,}"
                                      for i in sorted(items, key=lambda x: -x["raster_px"])[:5]))
    print(f"-> {os.path.join(out, 'frustum.json')}")
    return 0


def cmd_cost(args):
    import json
    from shaderopt.frame import cost as frame_cost
    from shaderopt.frame import report as frame_report
    from shaderopt.frame import variants
    path = os.path.join(args.frame, "frame_events.json")
    if not os.path.exists(path):
        sys.exit(f"no frame_events.json in {args.frame}: run `frame` first")
    with open(path, encoding="utf-8") as f:
        frame = json.load(f)
    events = frame["events"]
    fr_path = args.frustum or os.path.join(args.frame, "frustum.json")
    if os.path.isdir(fr_path):
        fr_path = os.path.join(fr_path, "frustum.json")
    frustum = None
    if os.path.exists(fr_path):
        with open(fr_path, encoding="utf-8") as f:
            frustum = json.load(f)
    elif args.frustum:
        sys.exit(f"no frustum.json at {args.frustum}")
    cores = parse_cores(args)
    project = args.project or frame.get("project")
    root = args.variants or os.path.join(args.frame, "variants")
    platforms = ["vulkan"] if args.vulkan_only else ["gles3", "vulkan"]
    try:
        state = variants.run(events, project, root, cores, platforms, args.jobs, compile_missing=not args.no_compile)
    except variants.VariantsError as e:
        sys.exit(f"variants failed: {e}")
    main_core = args.main_core if args.main_core in cores else cores[0]
    c = frame_cost.compute(frame, events, state, frustum, args.api, cores, main_core)
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    c["malioc"] = ", ".join(sorted({r["malioc"] for r in recs}))
    c["frame_dir"] = os.path.abspath(args.frame)
    c["frustum"] = os.path.abspath(fr_path) if frustum else None
    c["variants_dir"] = os.path.abspath(root)
    out = args.out or args.frame
    os.makedirs(out, exist_ok=True)
    frame_cost.write(c, out)
    frame_report.write(c, os.path.join(out, "frame_report.html"),
                       f"Стоимость кадра {os.path.basename(os.path.abspath(args.frame))}")

    cov, t = c["coverage"], c["totals"].get(c["main_core"])
    print(f"{cov['draws_priced']} of {cov['draws']} draws and {cov['events_priced'] - cov['draws_priced']} of "
          f"{cov['events'] - cov['draws']} dispatches priced, {len(state['keys'])} variants, {args.api}; pixels: "
          + ", ".join(f"{k} {v}" for k, v in c["pixel_methods"].items())
          + "; vertices: " + ", ".join(f"{k} {v}" for k, v in c["vertex_methods"].items())
          + (f"; frustum {fr_path}" if frustum else ""))
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
    for m in c["missing"]:
        print(f"  no price: #{m['index'] + 1} {m['stage']} {m['shader'] or ''} {m['pass'] or ''}: {m['reason']}")
    for name in ("frame_report.html", "frame_cost.csv", "frame_cost.json"):
        print(f"-> {os.path.abspath(os.path.join(out, name))}")
    return 0


def cmd_export(args):
    from shaderopt.unity import export
    cores = parse_cores(args) if args.measure else None
    out = args.out or os.path.join(REAL, os.path.basename(os.path.abspath(args.project)))
    try:
        res = export.export(args.project, args.shaders, out, args.platforms.split(","), args.mode, args.timeout)
    except export.ExportError as e:
        sys.exit(f"export failed: {e}")
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


def add_report_args(p):
    p.add_argument("--top", type=int, default=20, help="rows per table in report.html (default 20)")
    p.add_argument("--fp16-threshold", type=float, default=25, help="low_fp16 flag below this percent (default 25)")


def add_core_args(p):
    p.add_argument("--cores", default="Mali-G78",
                   help="comma separated cores and/or presets; preset:mobile = " + ",".join(core_presets.PRESETS["mobile"]))
    p.add_argument("--core", help="one core (same as --cores <core>)")
    p.add_argument("--jobs", type=int, help="parallel malioc runs (default: CPU count)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shaderopt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="measure every .vert / .frag (GLSL) and .vert.spv / .frag.spv "
                                       "(SPIR-V, Vulkan only) in a folder with malioc, then write the report")
    m.add_argument("folder")
    add_core_args(m)
    m.add_argument("--api", default="gles", choices=["gles", "vulkan"],
                   help="API for GLSL files (SPIR-V is always vulkan)")
    m.add_argument("--out", help="output folder (default: the measured folder)")
    m.add_argument("--no-report", action="store_true", help="skip report.html / report.csv")
    add_report_args(m)
    m.set_defaults(func=cmd_measure)

    r = sub.add_parser("report", help="report.html + report.csv from measurements.jsonl")
    r.add_argument("folder")
    r.add_argument("--out", help="output folder (default: the folder)")
    add_report_args(r)
    r.set_defaults(func=cmd_report)

    e = sub.add_parser("export", help="compile shaders of a Unity project into per-variant GLSL / SPIR-V files "
                                      "(in the open editor via Unity CLI, else in batchmode)")
    e.add_argument("shaders", nargs="+", help="asset path (Assets/.../X.shadergraph), folder under Assets/ "
                                              "or shader name (Universal Render Pipeline/Lit)")
    e.add_argument("--project", required=True, help="Unity project folder")
    e.add_argument("--out", help="output folder (default: shaderopt/corpus/real/<project name>, not in git)")
    e.add_argument("--platforms", default="gles3,vulkan", help="comma separated: gles3, vulkan")
    e.add_argument("--mode", default="auto", choices=["auto", "editor", "batch"],
                   help="auto: the open editor if it answers Unity CLI, else batchmode")
    e.add_argument("--timeout", type=int, default=1800, help="seconds")
    e.add_argument("--measure", action="store_true", help="measure the output folder with malioc and write the report")
    add_core_args(e)
    add_report_args(e)
    e.set_defaults(func=cmd_export)

    f = sub.add_parser("frame", help="snapshot the frame of the open Unity editor through the Frame Debugger")
    f.add_argument("--project", required=True, help="Unity project folder (its editor must be open)")
    f.add_argument("--out", help="output folder (default: shaderopt/out/frame_<project>_<time>, not in git)")
    f.add_argument("--timeout", type=int, default=1800, help="seconds")
    f.add_argument("--max-events", type=int, default=0, help="stop after N events (0 = all)")
    f.add_argument("--renderdoc", action="store_true",
                   help="also capture the same frame with RenderDoc (loaded in the editor: Game tab -> Load RenderDoc) "
                        "and take the pixels of every event from its PSInvocations")
    f.set_defaults(func=cmd_frame)

    fr = sub.add_parser("frustum", help="screen coverage of every renderer the game camera draws "
                                        "(raster with overdraw, and visible), in the open editor")
    fr.add_argument("--project", required=True, help="Unity project folder (its editor must be open)")
    fr.add_argument("--out", help="output folder (default: shaderopt/out/frustum_<project>_<time>, not in git)")
    fr.add_argument("--camera", default="", help="camera name (default: Camera.main)")
    fr.set_defaults(func=cmd_frustum)

    c = sub.add_parser("cost", help="cost of a frame snapshot: every event's shader variant measured with malioc, "
                                    "pixels x pixel price + vertices x vertex price, frame_report.html")
    c.add_argument("frame", help="snapshot folder with frame_events.json (from `frame`)")
    c.add_argument("--project", help="Unity project to compile missing variants in (default: the snapshot's; "
                                     "its editor must be open)")
    c.add_argument("--frustum", help="frustum.json or its folder (from `frustum`), refines pixels without RenderDoc "
                                     "(default: <frame>/frustum.json if present)")
    c.add_argument("--variants", help="folder of the compiled and measured variants (default: <frame>/variants; "
                                      "share one between frames of a project to compile less)")
    c.add_argument("--cores", default="preset:mobile",
                   help="comma separated cores and/or presets (default preset:mobile)")
    c.add_argument("--core", help="one core (same as --cores <core>)")
    c.add_argument("--main-core", default="Mali-G78",
                   help="core shown first and printed (decision D-17, default Mali-G78)")
    c.add_argument("--api", default="vulkan", choices=["vulkan", "gles"],
                   help="prices of this API (default vulkan: the Android API of the target project)")
    c.add_argument("--vulkan-only", action="store_true", help="compile only Vulkan variants (no GLES3)")
    c.add_argument("--no-compile", action="store_true", help="use only the variants already in the folder")
    c.add_argument("--jobs", type=int, help="parallel malioc runs (default: CPU count)")
    c.add_argument("--out", help="output folder (default: the snapshot folder)")
    c.set_defaults(func=cmd_cost)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
