"""One material of a Unity project: everything about its shader variants, and what every line of them costs.

The variants are planned, compiled and measured as for the comparison of materials (features/matcompare.py): the
material's keywords plus the pipeline's global keywords of every color pass. On top of the prices, every pass's
fragment and vertex shader is ablated statement by statement (core/ablation.py, plan A3.2) on the chosen cores,
dynamic loops at n iterations.

  run(project, material, root, cores, ...) -> JSON-able result (kind "material"); RESULT: its page
"""
import os
import sys

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import ablation as ablation_run
from paretogpu.app import materials
from paretogpu.app import results
from paretogpu.core import ablation
from paretogpu.core.materials import NS, apis_of, describe
from paretogpu.core.pricing import LOOP_ITERS
from paretogpu.core.variants import match
from paretogpu.features.common import JOBS, cores_args, default_variants, fail, parse_cores
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.model.cores import main_core
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE

STAGES = (("fragment", "frag"), ("vertex", "vert"))


def run(project, material, root, cores, platforms=("gles3", "vulkan"), api="vulkan", jobs=None,
        compile_missing=True, recompile=False, ns=NS, snapshot_folders=(), ablate_cores=None,
        n=LOOP_ITERS, rep=CONSOLE):
    if "gles3" not in platforms:
        raise ValueError("абляции нужен GLES-вариант (в нём остаются имена): не запускайте с --vulkan-only")
    prep = materials.prepare(project, [material], root, cores, platforms, jobs, compile_missing, recompile,
                             snapshot_folders, rep)
    apis = apis_of(api, platforms)
    side = describe(prep["mats"][0], prep["keys"][0], prep["state"], cores, apis, ns)
    ablate_cores = [c for c in (ablate_cores or cores[:1]) if c in cores] or cores[:1]
    jobs_ = []
    for p, k in zip(side["passes"], prep["keys"][0]):
        recs, why = match(k.as_event(), prep["state"], "gles")
        for stage, short in STAGES:
            fn = ((recs or {}).get(stage) and next(iter(recs[stage].values()))["file"])
            if fn:
                jobs_.append((p, stage, os.path.join(root, fn)))
            else:
                p.setdefault("ablation", {})[stage] = {"error": why or "GLES-вариант не скомпилирован"}
    total = 0
    for p, stage, path in jobs_:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        try:
            total += sum(1 for s in ablation.parse(src, stage)["statements"] if s["ablate"]) * len(ablate_cores)
        except (ValueError, StopIteration):
            pass
    rep.phase("ablation", total)
    tick = rep.counter(total)
    for p, stage, path in jobs_:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        try:
            p.setdefault("ablation", {})[stage] = ablation_run.run(src, stage, ablate_cores, n, jobs, rep, tick)
        except (ValueError, StopIteration) as e:
            p.setdefault("ablation", {})[stage] = {"error": f"не удалось разобрать main(): {e}"}
    rep.phase("report")
    return {"kind": "material", **materials.common(prep, [side], cores, api, apis, ns), "m": side,
            "ablate_cores": ablate_cores, "ablate_n": n}


def run_command(args):
    cores = parse_cores(args)
    ablate = [c.strip() for c in args.ablate_cores.split(",")] if args.ablate_cores else [main_core(cores)]
    bad = [c for c in ablate if c not in cores]
    if bad:
        sys.exit(f"--ablate-cores {', '.join(bad)}: not among the cores ({', '.join(cores)})")
    project = os.path.abspath(args.project)
    root = args.variants or workspace.variants_dir(project)
    out = args.out or workspace.new_result_dir("matshader")
    try:
        res = run(project, args.material, root, cores, ["gles3", "vulkan"], args.api, args.jobs,
                  compile_missing=not args.no_compile, recompile=args.recompile, snapshot_folders=[workspace.OUT],
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
    print(f"-> {os.path.abspath(results.publish(RESULT, res, out))}")
    return 0


RESULT = ResultKind("matshader", lambda r: f"Материал: {r['m']['material']}",
                    lambda r: {"m": {k: r["m"].get(k) for k in ("material", "path", "shader")}})

MATSHADER = Command(
    "matshader", "one material of a Unity project: its shader variants (material and pipeline keywords, color "
                 "passes), their prices and what every line of their code costs (ablation, plan A3.2); needs the open "
                 "editor to compile",
    [Arg("material", help=".mat path, relative to the project or absolute"),
     Arg("--project", required=True, help="Unity project folder (its editor must be open to compile)"),
     Arg("--variants", help="folder of the compiled variants (default: paretogpu/out/variants_<project>)"),
     cores_args(),
     Arg("--ablate-cores", help="cores to ablate on, comma separated (default Mali-G78): every statement is one malioc "
                                "run per core"),
     Arg("--n", type=int, default=LOOP_ITERS, help="iterations of the dynamic loops in the ablation (default %(default)s)"),
     Arg("--api", default="vulkan", choices=["vulkan", "gles"], help="main API of the prices (default vulkan)"),
     Arg("--no-compile", action="store_true", help="use only the variants already in the folder"),
     Arg("--recompile", action="store_true", help="compile the material's variants again"),
     JOBS,
     Arg("--out", help="output folder (default: paretogpu/out/_matshader/<time>)")],
    run_command, phases=lambda v: ["materials", "fingerprints", "compile", "measure", "loops", "ablation", "report"],
    report=lambda v: os.path.join(v["out"], "matshader.html"),
    ui_values=lambda v: default_variants({**v, "out": workspace.new_result_dir("matshader")}), result=RESULT)
