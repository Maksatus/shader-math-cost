"""One material of a Unity project: everything about its shader variants, and what every line of them costs.

The variants are planned, compiled and measured as for the comparison of materials (project/matcompare.py): the
material's keywords plus the pipeline's global keywords of every color pass. On top of the prices, every pass's
fragment and vertex shader is ablated statement by statement (project/ablation.py, plan A3.2) on the chosen cores,
dynamic loops at n iterations.

  run(project, material, root, cores, ...) -> JSON-able result (kind "material"); render_html() -> compare_view.html
"""
import json
import os

from paretogpu import progress as progress_ui
from paretogpu.frame import compare
from paretogpu.frame import cost as frame_cost
from paretogpu.frame import variants
from paretogpu.project import ablation, matcompare, shaders

STAGES = (("fragment", "frag"), ("vertex", "vert"))


def run(project, material, root, cores, platforms=("gles3", "vulkan"), api="vulkan", jobs=None,
        compile_missing=True, recompile=False, ns=matcompare.NS, snapshot_folders=(), ablate_cores=None,
        n=frame_cost.LOOP_ITERS, progress=print):
    if "gles3" not in platforms:
        raise ValueError("абляции нужен GLES-вариант (в нём остаются имена): не запускайте с --vulkan-only")
    prep = matcompare.prepare(project, [material], root, cores, platforms, jobs, compile_missing, recompile,
                              snapshot_folders, progress)
    apis = matcompare.apis_of(api, platforms)
    side = matcompare.describe(prep["mats"][0], prep["keys"][0], prep["state"], cores, apis, ns)
    ablate_cores = [c for c in (ablate_cores or cores[:1]) if c in cores] or cores[:1]
    jobs_ = []
    for p, k in zip(side["passes"], prep["keys"][0]):
        recs, why = variants.match(shaders.pseudo_events([k])[0], prep["state"], "gles")
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
    progress_ui.phase("ablation", total)
    tick = progress_ui.counter(total)
    for p, stage, path in jobs_:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        try:
            p.setdefault("ablation", {})[stage] = ablation.run(src, stage, ablate_cores, n, jobs, progress, tick)
        except (ValueError, StopIteration) as e:
            p.setdefault("ablation", {})[stage] = {"error": f"не удалось разобрать main(): {e}"}
    progress_ui.phase("report")
    return {"kind": "material", **matcompare.common(prep, [side], cores, api, apis, ns), "m": side,
            "ablate_cores": ablate_cores, "ablate_n": n}


def render_html(res):
    return compare.render_html(res, f"Материал: {res['m']['material']}")


def write(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "matshader.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "matshader.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_html(res))
    return os.path.join(out_dir, "matshader.html")
