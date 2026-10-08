"""Two materials of a Unity project against each other: the price of their shader variants per pixel and vertex.

A material selects its variants (core/materials.py): its shader, the color passes it is drawn with, its keywords
plus the global keywords the pipeline sets for that pass (seen in the project's snapshots). Both materials'
variants are compiled in the open editor and measured with malioc like a frame's (app/variants.py, the same
variants folder of the project, so a variant is compiled once), and dynamic loops (lights, probes, ray steps) are
priced at every n of NS (core/loops.py). No frame is needed: the result is the price of one pixel / vertex.

Passes of A and B are paired by name, the rest in order (another shader may name its passes differently).

  run(project, a, b, root, cores, ...) -> JSON-able result; render_html() puts it into views/templates/result_view.html
"""
from paretogpu.app import materials
from paretogpu.core.materials import NS, pair_passes, apis_of, describe
from paretogpu.views import html
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


def render_html(res):
    return html.render_result(res, f"Материалы: {res['a']['material']} → {res['b']['material']}")


def write(res, out_dir):
    return html.write_result(res, out_dir, "matcompare", f"Материалы: {res['a']['material']} → {res['b']['material']}")
