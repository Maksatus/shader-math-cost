import os

from paretogpu.app import matshader
from paretogpu.app import results
from paretogpu.core.pricing import LOOP_ITERS
from paretogpu.features.common import JOBS, cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.model.cores import main_core
from paretogpu.model.errors import ParetoError
from paretogpu.store import workspace
from paretogpu.views import messages


def run_command(args):
    cores = parse_cores(args)
    ablate = [c.strip() for c in args.ablate_cores.split(",")] if args.ablate_cores else [main_core(cores)]
    bad = [c for c in ablate if c not in cores]
    if bad:
        fail(f"--ablate-cores {', '.join(bad)}: not among the cores ({', '.join(cores)})", code="input")
    project = os.path.abspath(args.project)
    root = args.variants or workspace.variants_dir(project)
    out = args.out or workspace.new_result_dir("matshader")
    try:
        res = matshader.run(project, args.material, root, cores, ["gles3", "vulkan"], args.api, args.jobs,
                            compile_missing=not args.no_compile, recompile=args.recompile,
                            snapshot_folders=[workspace.OUT], ablate_cores=ablate, n=args.n)
    except ParetoError as e:
        fail(f"matshader: {e}", e)
    for w in res["warnings"]:
        print(f"  ! {messages.text(w)}")
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
    print(f"-> {os.path.abspath(results.publish(RESULT, res, out))}")
    return 0


RESULT = ResultKind("matshader", lambda r: f"Материал: {r['m']['material']}",
                    lambda r: {"m": {k: r["m"].get(k) for k in ("material", "path", "shader")}})

MATSHADER = Command(
    "matshader", "one material of a Unity project: its shader variants (material and pipeline keywords, color "
                 "passes), their prices and what every line of their code costs (ablation, plan A3.2); needs the open "
                 "editor to compile",
    [Arg("material", label="Материал", help=".mat path, relative to the project or absolute"),
     Arg("--project", label="Проект", required=True, help="Unity project folder (its editor must be open to compile)"),
     Arg("--variants", label="Папка скомпилированных вариантов", help="folder of the compiled variants (default: paretogpu/out/variants_<project>)"),
     cores_args(),
     Arg("--ablate-cores", label="Ядра для разбора по строкам", help="cores to ablate on, comma separated (default Mali-G78): every statement is one malioc "
                                "run per core"),
     Arg("--n", label="Итераций динамических циклов", type=int, default=LOOP_ITERS, help="iterations of the dynamic loops in the ablation (default %(default)s)"),
     Arg("--api", label="API цен", default="vulkan", choices=["vulkan", "gles"], help="main API of the prices (default vulkan)"),
     Arg("--no-compile", label="Не компилировать", action="store_true", help="use only the variants already in the folder"),
     Arg("--recompile", label="Скомпилировать заново", action="store_true", help="compile the material's variants again"),
     JOBS,
     Arg("--out", label="Папка результата", help="output folder (default: paretogpu/out/_matshader/<time>)")],
    run_command, title="Разбор материала",
    phases=lambda v: ["materials", "fingerprints", "compile", "measure", "loops", "ablation", "report"],
    report=lambda v: os.path.join(v["out"], RESULT.page),
    ui_values=lambda v: default_variants({**v, "out": workspace.new_result_dir("matshader")}), result=RESULT)
