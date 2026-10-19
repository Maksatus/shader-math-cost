import os

from paretogpu.app import results
from paretogpu.core import compare
from paretogpu.features.common import fail
from paretogpu.features.spec import Arg, Command, ResultKind
from paretogpu.store import cost_runs
from paretogpu.views import messages


def run_command(args):
    try:
        a, b = cost_runs.load(args.a), cost_runs.load(args.b)
    except (OSError, ValueError) as e:
        fail(f"cannot read a cost run: {e}", e, "input")
    cmp = compare.compare(a, b)
    for w in cmp["warnings"]:
        print(f"  ! {messages.text(w)}")
    x = cmp["by_core"].get(cmp["main_core"])
    if x:
        pct = f" ({100 * x['delta'] / x['a']:+.1f}%)" if x["a"] else ""
        print(f"{x['core']}: {x['a'] / 1e6:.1f} -> {x['b'] / 1e6:.1f} M cycles, {x['delta'] / 1e6:+.2f} M{pct}: "
              f"price {x['price'] / 1e6:+.2f}, loops {x['loops'] / 1e6:+.2f}, work {x['work'] / 1e6:+.2f}, "
              f"mix {x['mix'] / 1e6:+.2f}"
              + ("  (the same snapshot: only prices and loop trip counts differ)" if cmp["same_frame"] else ""))
        for s in x["shaders"][:10]:
            if abs(s["delta"]) < 1:
                break
            print(f"  {s['delta'] / 1e6:+8.2f} M  {s['shader'][:50]:50s} price {s['price'] / 1e6:+.2f}, "
                  f"loops {s['loops'] / 1e6:+.2f}, work {s['work'] / 1e6:+.2f}, mix {s['mix'] / 1e6:+.2f}" + (f"  [{s['status']}]" if s["status"] != "both" else ""))
    out = args.out or (args.b if os.path.isdir(args.b) else os.path.dirname(os.path.abspath(args.b)))
    print(f"-> {os.path.abspath(results.publish(RESULT, cmp, out))}")
    return 0


def _brief(cmp):
    x = compare.brief(cmp) or {}
    return {"a": cmp["a"].get("snapshot"), "b": cmp["b"].get("snapshot"), "core": x.get("core"),
            "a_total": x.get("a"), "b_total": x.get("b"), "delta": x.get("delta")}


RESULT = ResultKind("compare", lambda c: "Сравнение frames", _brief)

COMPARE = Command(
    "compare", "compare two cost runs: the frame, its stages and shaders, every change split into price (the shaders), "
               "loops (trip counts of their dynamic loops), work (pixels, vertices) and mix (variants)",
    [Arg("a", label="Расчёт A (до)", help="before: snapshot folder (its latest cost), frame_cost.json or <snapshot>/costs/<time>.json"),
     Arg("b", label="Расчёт B (после)", help="after: the same kinds"),
     Arg("--out", label="Папка результата", help="folder for compare.html and compare.json (default: B's folder)")],
    run_command, result=RESULT)
