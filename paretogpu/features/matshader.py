"""One material of a Unity project: everything about its shader variants, and what every line of them costs.

The variants are planned, compiled and measured as for the comparison of materials (features/matcompare.py): the
material's keywords plus the pipeline's global keywords of every color pass. On top of the prices, every pass's
fragment and vertex shader is ablated statement by statement (core/ablation.py, plan A3.2) on the chosen cores,
dynamic loops at n iterations.

  run(project, material, root, cores, ...) -> JSON-able result (kind "material"); render_html() -> result_view.html
"""
import os

from paretogpu.app import ablation as ablation_run
from paretogpu.app import materials
from paretogpu.core import ablation
from paretogpu.core.materials import NS, apis_of, describe
from paretogpu.core.pricing import LOOP_ITERS
from paretogpu.core.variants import match
from paretogpu.views import html
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


def render_html(res):
    return html.render_result(res, f"Материал: {res['m']['material']}")


def write(res, out_dir):
    return html.write_result(res, out_dir, "matshader", f"Материал: {res['m']['material']}")
