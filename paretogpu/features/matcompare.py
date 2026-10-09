"""Two materials of a Unity project against each other: the price of their shader variants per pixel and vertex.

A material selects its variants (core/materials.py): its shader, the color passes it is drawn with, its keywords
plus the global keywords the pipeline sets for that pass (seen in the project's snapshots). Both materials'
variants are compiled in the open editor and measured with malioc like a frame's (app/variants.py, the same
variants folder of the project, so a variant is compiled once), and dynamic loops (lights, probes, ray steps) are
priced at every n of NS (core/loops.py). No frame is needed: the result is the price of one pixel / vertex.

Passes of A and B are paired by name, the rest in order (another shader may name its passes differently).

  run(project, a, b, root, cores, ...) -> JSON-able result; RESULT: its page (views/templates/result_view.html)
"""
import os

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import materials
from paretogpu.app import results
from paretogpu.core.materials import NS, apis_of, describe, pair_passes
from paretogpu.features.common import JOBS, cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


def run(project, a, b, root, cores, platforms=("gles3", "vulkan"), api="vulkan", jobs=None, compile_missing=True,
        recompile=False, ns=NS, snapshot_folders=(), rep=CONSOLE):
    prep = materials.prepare(project, [a, b], root, cores, platforms, jobs, compile_missing, recompile,
                             snapshot_folders, rep)
    rep.phase("report")
    apis = apis_of(api, platforms)
    sides = [describe(m, keys, prep["state"], cores, apis, ns) for m, keys in zip(prep["mats"], prep["keys"])]
    return {"kind": "materials", **materials.common(prep, sides, cores, api, apis, ns), "a": sides[0], "b": sides[1],
            "pairs": pair_passes([p["pass"] for p in sides[0]["passes"]], [p["pass"] for p in sides[1]["passes"]])}


def run_command(args):
    cores = parse_cores(args)
    project = os.path.abspath(args.project)
    root = args.variants or workspace.variants_dir(project)
    out = args.out or workspace.new_result_dir("matcompare")
    try:
        res = run(project, args.a, args.b, root, cores, ["vulkan"] if args.vulkan_only else ["gles3", "vulkan"], args.api,
                  args.jobs, compile_missing=not args.no_compile, recompile=args.recompile,
                  snapshot_folders=[workspace.OUT])
    except (ValueError, VariantsError) as e:
        fail(f"matcompare: {e}", e)
    for w in res["warnings"]:
        print(f"  ! {w}")
    mc = res["main_core"]
    for ia, ib in res["pairs"]:
        pa = res["a"]["passes"][ia] if ia is not None else None
        pb = res["b"]["passes"][ib] if ib is not None else None
        fmt = lambda p, k: "/".join(f"{p['prices'][mc][k][str(n)]:.1f}" for n in res["ns"]) \
            if p and mc in p["prices"] else "-"
        print(f"{mc} {(pa or pb)['pass']}: pixel A {fmt(pa, 'px')}  B {fmt(pb, 'px')}; vertex A {fmt(pa, 'vtx')}  "
              f"B {fmt(pb, 'vtx')}  (cycles at n = {'/'.join(map(str, res['ns']))})")
    print(f"-> {os.path.abspath(results.publish(RESULT, res, out))}")
    return 0


def _material(m):
    return {k: m.get(k) for k in ("material", "path", "shader")}


RESULT = ResultKind("matcompare", lambda r: f"Материалы: {r['a']['material']} → {r['b']['material']}",
                    lambda r: {"a": _material(r["a"]), "b": _material(r["b"])})

MATCOMPARE = Command(
    "matcompare", "two materials of a Unity project against each other: their shader variants (material and pipeline "
                  "keywords, color passes) per pixel and vertex, dynamic loops at n = 0..8; needs the open editor to "
                  "compile",
    [Arg("a", help="material A: .mat path, relative to the project or absolute"),
     Arg("b", help="material B"),
     Arg("--project", required=True, help="Unity project folder (its editor must be open to compile)"),
     Arg("--variants", help="folder of the compiled variants (default: paretogpu/out/variants_<project>, the one the "
                            "frames of the project share)"),
     cores_args(),
     Arg("--api", default="vulkan", choices=["vulkan", "gles"], help="prices of this API (default vulkan)"),
     Arg("--vulkan-only", action="store_true", help="compile only Vulkan variants (no GLES3)"),
     Arg("--no-compile", action="store_true", help="use only the variants already in the folder"),
     Arg("--recompile", action="store_true", help="compile the materials' variants again"),
     JOBS,
     Arg("--out", help="output folder (default: paretogpu/out/_matcompare/<time>)")],
    run_command, phases=lambda v: ["materials", "fingerprints", "compile", "measure", "loops", "report"],
    report=lambda v: os.path.join(v["out"], "matcompare.html"),
    ui_values=lambda v: default_variants({**v, "out": workspace.new_result_dir("matcompare")}), result=RESULT)
