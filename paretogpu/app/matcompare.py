"""Two materials compared without a frame: price of one pixel / vertex, loops at every n of NS; passes paired by
name, the rest in order."""
from paretogpu.app import materials
from paretogpu.core.materials import NS, describe, pair_passes
from paretogpu.views.reporter import CONSOLE


def run(project, a, b, root, cores, jobs=None, compile_missing=True, recompile=False, ns=NS, snapshot_folders=(),
        rep=CONSOLE):
    prep = materials.prepare(project, [a, b], root, cores, jobs, compile_missing, recompile, snapshot_folders, rep)
    rep.phase("report")
    sides = [describe(m, keys, prep["state"], cores, ns) for m, keys in zip(prep["mats"], prep["keys"])]
    return {"kind": "materials", **materials.common(prep, sides, cores, ns), "a": sides[0], "b": sides[1],
            "pairs": pair_passes([p["pass"] for p in sides[0]["passes"]], [p["pass"] for p in sides[1]["passes"]])}
