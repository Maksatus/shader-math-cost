"""Why the heaviest shaders of a frame are heavy (plan A3.5, report v2): what `hotspots` runs (features/hotspots.py).

For the costliest shader variants of a priced snapshot (frame_cost.json of `cost`, by share of the frame on the main
core) the GLES file of the same variant (in the variants folder next to the Vulkan one; compiled now if the cost did
not need it) is ablated statement by statement (core/ablation.py): its costliest parts with their pipes and what they
do (texture reads, buffer reads in a light loop, sin, division, hash...), and the candidates for moving to the vertex
shader (core/vertexmove.py). Dynamic loops at the frame's n.

  run(frame_dir, top, core, jobs) -> JSON-able result (kind "hotspots")
"""
import json
import os
import time

from paretogpu.adapters.unity.variants import VariantsError
from paretogpu.app import ablation as ablation_run
from paretogpu.app import variants
from paretogpu.core import ablation
from paretogpu.core import loops
from paretogpu.core.pricing import LOOP_ITERS, loop_n
from paretogpu.model.errors import ParetoError
from paretogpu.model.variant import VariantKey
from paretogpu.store import workspace
from paretogpu.store.variant_store import gles_file
from paretogpu.views.reporter import CONSOLE

VERTEX_SHARE = 0.25  # the vertex shader is ablated too when it is at least this part of the variant's cost


def compile_gles(frame_dir, c, root, items, rep=CONSOLE):
    """Compile the GLES files the items lack in the open editor: None, or why they could not be."""
    need = {it["variant"] for it in items if any(not gles_file(root, vf) for vf in it["files"].values())}
    if not need:
        return None
    snap_dir = next((d for d in (frame_dir, c.get("frame_dir") or "")
                     if d and os.path.isfile(os.path.join(d, workspace.SNAPSHOT_FILE))), None)
    if not snap_dir:
        return f"no {workspace.SNAPSHOT_FILE} next to the cost or in {c.get('frame_dir')}"
    with open(os.path.join(snap_dir, workspace.SNAPSHOT_FILE), encoding="utf-8") as f:
        events = json.load(f)["events"]
    keys = {str(k): k for k in (VariantKey.of(e) for e in events if e["kind"] == "draw")}
    rep.log(f"compiling the GLES files of {len(need)} variants ...")
    try:
        variants.compile_keys([keys[v] for v in sorted(need) if v in keys], (c.get("frame") or {}).get("project"),
                              root, ["gles3"], rep)
    except VariantsError as e:
        return str(e)
    return None


def plan(c, top, core):
    """The variants to analyse: [{"variant", "shader", "pass", "keywords", "share", "fragment", "vertex", "pixels",
    "vertices", "files": {stage: vulkan file}}], costliest first."""
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
    """The costliest `top` variants of the priced snapshot in frame_dir on `core` (default: its main core), ablated:
    the document of hotspots.json."""
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
        n, _ = loop_n(it["shader"], loops_.get("n", LOOP_ITERS), loops_.get("overrides"))
        it["n"] = n
        # the fragment's loops at the trip counts the frame priced them with (one per outer loop, core/frame_cost)
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
