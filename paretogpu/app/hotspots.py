"""Ablation of the costliest variants of a priced snapshot, on their GLES files (compiled now if the cost did not
need them); dynamic loops at the frame's n."""
import json
import os
import time

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import ablation as ablation_run
from paretogpu.app import variants
from paretogpu.core import ablation
from paretogpu.core import loops
from paretogpu.core.pricing import LOOP_ITERS
from paretogpu.model.errors import ParetoError
from paretogpu.model.variant import CODE, VariantKey
from paretogpu.store import workspace
from paretogpu.store.variant_store import gles_file
from paretogpu.views.reporter import CONSOLE

VERTEX_SHARE = 0.25  # the vertex shader is ablated too when it is at least this part of the variant's cost


def compile_gles(frame_dir, c, root, items, rep=CONSOLE):
    """-> None, or why the missing GLES files could not be compiled."""
    need = {it["variant"] for it in items if any(not gles_file(root, vf) for vf in it["files"].values())}
    if not need:
        return None
    keys = {e["variant"]: VariantKey.from_list(e["key"]) for e in c.get("events") or [] if e.get("key")}
    todo = [keys[v] for v in sorted(need) if v in keys and not any(kw.startswith(CODE) for kw in keys[v].keywords)]
    if not todo:
        return None
    rep.log(f"compiling the GLES files of {len(todo)} variants ...")
    try:
        variants.compile_keys(todo, (c.get("frame") or {}).get("project"), root, ["gles3"], rep)
    except VariantsError as e:
        return str(e)
    return None


def plan(c, top, core):
    """Costliest first; "files" are {stage: vulkan file}."""
    groups = ((c.get("groups") or {}).get(core) or {}).get("variant") or []
    by_variant = {}
    for e in c.get("events") or []:
        if e.get("kind") == "draw" and e.get("files") and core in (e.get("cost") or {}):
            by_variant.setdefault(e["variant"], e)
    out = []
    for g in groups:
        e = by_variant.get(g["key"])
        if not e:
            continue  # compute or unpriced
        out.append({"variant": g["key"], "shader": e.get("shader"), "pass": e.get("pass"), "keywords": e.get("keywords") or [],
                    "share": g.get("share"), "total": g.get("total"), "fragment": g.get("fragment"), "vertex": g.get("vertex"),
                    "pixels": g.get("pixels"), "vertices": g.get("vertices"), "events": g.get("events"), "files": e["files"],
                    "loop": e.get("loop")})
        if len(out) >= top:
            break
    return out


def run(frame_dir, top=10, core=None, jobs=None, rep=CONSOLE):
    with open(os.path.join(frame_dir, workspace.COST_FILE), encoding="utf-8") as f:
        c = json.load(f)
    core = core or c.get("main_core")
    if core not in [x["name"] for x in c.get("cores") or []]:
        raise ParetoError(f"{core}: the snapshot is not priced on it", "input")
    root = c.get("variants_dir") or os.path.join(frame_dir, "variants")
    loops_ = c.get("loops") or {}
    items = plan(c, top, core)
    why = compile_gles(frame_dir, c, root, items, rep)
    work, warn = [], []
    for it in items:
        it["ablation"] = {}
        stages = ["fragment"] + (["vertex"] if (it.get("vertex") or 0) >= VERTEX_SHARE * (it.get("total") or 1) else [])
        n = it["n"] = loops_.get("n", LOOP_ITERS)
        # trip counts the frame priced with, one per outer loop
        frame_ns = [x["n"] for x in ((it.get("loop") or {}).get("loops") or [])]
        for stage in stages:
            vf = it["files"].get(stage)
            gf = vf and gles_file(root, vf)
            if not gf:
                it["ablation"][stage] = {"error": f"нет GLES-варианта: {why or 'Unity его не скомпилировал'}"}
                continue
            with open(gf, encoding="utf-8", errors="replace") as f:
                src = f.read()
            try:
                count = sum(1 for s in ablation.parse(src, stage)["statements"] if s["ablate"])
            except (ValueError, StopIteration) as e:
                it["ablation"][stage] = {"error": f"не удалось разобрать main(): {e}"}
                continue
            ns = frame_ns if stage == "fragment" and frame_ns and len(loops.outer_loops(src) or []) == len(frame_ns) else n
            work.append((it, stage, src, ns, count))
    rep.phase("ablation", sum(w[4] for w in work))
    tick = rep.counter(sum(w[4] for w in work))
    for it, stage, src, n, _ in work:
        rep.log(f"{it['variant']} {stage}:")
        it["ablation"][stage] = ablation_run.run(src, stage, [core], n, jobs, rep, tick)
    rep.phase("report")
    t = ((c.get("totals") or {}).get(core) or {}).get("total")
    return {"kind": "hotspots", "snapshot": os.path.basename(os.path.abspath(frame_dir)),
            "frame_dir": os.path.abspath(frame_dir), "project": (c.get("frame") or {}).get("project"), "core": core,
            "api": c.get("api"), "malioc": c.get("malioc"), "frame_total": t, "cost_computed_at": c.get("computed_at"),
            "shaders": items, "warnings": warn, "computed_at": time.time()}
