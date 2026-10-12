"""Two materials of a Unity project against each other: `python -m paretogpu matcompare` (app/matcompare.py).

The price of their shader variants per pixel and vertex, the dynamic loops at every n of NS; the passes of A and B
paired by name, the rest in order. RESULT: its page (views/templates/result_view.html).
"""
import os

from paretogpu.app import matcompare
from paretogpu.app import results
from paretogpu.features.common import JOBS, cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.model.errors import ParetoError
from paretogpu.store import workspace
from paretogpu.views import messages


def run_command(args):
    cores = parse_cores(args)
    project = os.path.abspath(args.project)
    root = args.variants or workspace.variants_dir(project)
    out = args.out or workspace.new_result_dir("matcompare")
    try:
        res = matcompare.run(project, args.a, args.b, root, cores, ["vulkan"] if args.vulkan_only else ["gles3", "vulkan"],
                             args.api, args.jobs, compile_missing=not args.no_compile, recompile=args.recompile,
                             snapshot_folders=[workspace.OUT])
    except ParetoError as e:
        fail(f"matcompare: {e}", e)
    for w in res["warnings"]:
        print(f"  ! {messages.text(w)}")
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
    [Arg("a", label="Материал A", help="material A: .mat path, relative to the project or absolute"),
     Arg("b", label="Материал B", help="material B"),
     Arg("--project", label="Проект", required=True, help="Unity project folder (its editor must be open to compile)"),
     Arg("--variants", label="Папка скомпилированных вариантов", help="folder of the compiled variants (default: paretogpu/out/variants_<project>, the one the "
                            "frames of the project share)"),
     cores_args(),
     Arg("--api", label="API цен", default="vulkan", choices=["vulkan", "gles"], help="prices of this API (default vulkan)"),
     Arg("--vulkan-only", action="store_true", help="compile only Vulkan variants (no GLES3)", label="Только Vulkan"),
     Arg("--no-compile", label="Не компилировать", action="store_true", help="use only the variants already in the folder"),
     Arg("--recompile", label="Скомпилировать заново", action="store_true", help="compile the materials' variants again"),
     JOBS,
     Arg("--out", label="Папка результата", help="output folder (default: paretogpu/out/_matcompare/<time>)")],
    run_command, title="Сравнение материалов",
    phases=lambda v: ["materials", "fingerprints", "compile", "measure", "loops", "report"],
    report=lambda v: os.path.join(v["out"], RESULT.page),
    ui_values=lambda v: default_variants({**v, "out": workspace.new_result_dir("matcompare")}), result=RESULT)
