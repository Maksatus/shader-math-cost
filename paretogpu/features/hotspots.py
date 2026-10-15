import os

from paretogpu.app import hotspots
from paretogpu.app import results
from paretogpu.features.common import JOBS, fail, positive_int
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.model.errors import ParetoError
from paretogpu.store import workspace
from paretogpu.views import messages


def run_command(args):
    if not os.path.exists(os.path.join(args.frame, workspace.COST_FILE)):
        fail(f"hotspots: no {workspace.COST_FILE} in {args.frame}: price the snapshot first (cost)", code="input")
    try:
        res = hotspots.run(args.frame, args.top, args.core, args.jobs, compile_missing=not args.no_compile)
    except ParetoError as e:
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
                              f"({messages.explain_text(a['statements'][sid]['explain'])})" for sid in roots[:3]))
            if a.get("vertex_candidates"):
                print(f"        to the vertex shader: {len(a['vertex_candidates'])} candidates")
    print(f"-> {os.path.abspath(results.publish(RESULT, res, args.out or args.frame, res['snapshot']))}")
    return 0


RESULT = ResultKind("hotspots", lambda r: f"Почему тяжёлые: {r['snapshot']}",
                    lambda r: {"core": r.get("core"), "frame_total": r.get("frame_total"),
                               "shaders": [s.get("variant") for s in r.get("shaders") or []]},
                    page=workspace.HOTSPOTS_PAGE)

HOTSPOTS = Command(
    "hotspots", "why the heaviest shaders of a priced snapshot are heavy: every line of their GLES variant ablated "
                "(plan A3.5), the costliest parts and what they do, the candidates for moving to the vertex shader "
                "-> hotspots.html",
    [Arg("frame", label="Снимок", help="snapshot folder with frame_cost.json (after `cost`)"),
     Arg("--top", label="Сколько шейдеров", type=positive_int, default=10, help="how many of the costliest variants (default %(default)s)"),
     Arg("--core", label="Ядро", help="core to ablate on (default: the snapshot's main core)"),
     JOBS,
     Arg("--no-compile", label="Не компилировать", action="store_true",
         help="use only the GLES files already in the variants folder (no Unity)"),
     Arg("--out", label="Папка результата", help="output folder (default: the snapshot folder)")],
    run_command, phases=lambda v: ["ablation", "report"], title="Почему тяжёлые шейдеры",
    report=lambda v: os.path.join(v.get("out") or v["frame"], RESULT.page), result=RESULT)
