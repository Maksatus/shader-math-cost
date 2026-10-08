"""Why the heaviest shaders of a frame are heavy (plan A3.5, report v2).

For the costliest shader variants of a priced snapshot (frame_cost.json of `cost`, by share of the frame on the main
core) the GLES file of the same variant (compiled next to the Vulkan one in the variants folder) is ablated
statement by statement (project/ablation.py): its costliest parts with their pipes and what they do (texture
reads, buffer reads in a light loop, sin, division, hash...), and the candidates for moving to the vertex shader
(project/vertexmove.py). Dynamic loops at the frame's n.

  run(frame_dir, top, core, jobs) -> JSON-able result (kind "hotspots"); write() -> <frame>/hotspots.html
"""
import json
import os
import time

from paretogpu import progress as progress_ui
from paretogpu.frame import compare
from paretogpu.frame import cost as frame_cost
from paretogpu.project import ablation

VERTEX_SHARE = 0.25  # the vertex shader is ablated too when it is at least this part of the variant's cost


def gles_file(root, vulkan_file):
    """The GLES file of a variant from its Vulkan one: <shader>_vulkan/<name>.frag.spv -> <shader>/<name>.frag."""
    folder, name = os.path.split(vulkan_file.replace("\\", "/"))
    if folder.endswith("_vulkan"):
        folder = folder[:-len("_vulkan")]
    if name.endswith(".spv"):
        name = name[:-len(".spv")]
    path = os.path.join(root, folder, name)
    return path if os.path.isfile(path) else None


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
                    "pixels": g.get("pixels"), "vertices": g.get("vertices"), "events": g.get("events"), "files": e["files"]})
        if len(out) >= top:
            break
    return out


def run(frame_dir, top=10, core=None, jobs=None, progress=print):
    path = os.path.join(frame_dir, "frame_cost.json")
    if not os.path.exists(path):
        raise ValueError(f"no frame_cost.json in {frame_dir}: price the snapshot first (cost)")
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    core = core or c.get("main_core")
    if core not in [x["name"] for x in c.get("cores") or []]:
        raise ValueError(f"{core}: the snapshot is not priced on it")
    root = c.get("variants_dir") or os.path.join(frame_dir, "variants")
    loops_ = c.get("loops") or {}
    items = plan(c, top, core)
    work, warn = [], []
    for it in items:
        it["ablation"] = {}
        stages = ["fragment"] + (["vertex"] if (it.get("vertex") or 0) >= VERTEX_SHARE * (it.get("total") or 1) else [])
        n, _ = frame_cost.loop_n(it["shader"], loops_.get("n", frame_cost.LOOP_ITERS), loops_.get("overrides"))
        it["n"] = n
        for stage in stages:
            vf = it["files"].get(stage)
            gf = vf and gles_file(root, vf)
            if not gf:
                it["ablation"][stage] = {"error": "нет GLES-варианта: снимок посчитан только для Vulkan (--vulkan-only)"}
                continue
            with open(gf, encoding="utf-8", errors="replace") as f:
                src = f.read()
            try:
                count = sum(1 for s in ablation.parse(src, stage)["statements"] if s["ablate"])
            except (ValueError, StopIteration) as e:
                it["ablation"][stage] = {"error": f"не удалось разобрать main(): {e}"}
                continue
            work.append((it, stage, src, n, count))
    progress_ui.phase("ablation", sum(w[4] for w in work))
    tick = progress_ui.counter(sum(w[4] for w in work))
    for it, stage, src, n, _ in work:
        progress(f"{it['variant']} {stage}:")
        it["ablation"][stage] = ablation.run(src, stage, [core], n, jobs, progress, tick)
    progress_ui.phase("report")
    t = ((c.get("totals") or {}).get(core) or {}).get("total")
    return {"kind": "hotspots", "snapshot": os.path.basename(os.path.abspath(frame_dir)),
            "frame_dir": os.path.abspath(frame_dir), "project": (c.get("frame") or {}).get("project"), "core": core,
            "api": c.get("api"), "malioc": c.get("malioc"), "frame_total": t, "cost_computed_at": c.get("computed_at"),
            "shaders": items, "warnings": warn, "computed_at": time.time()}


def render_html(res):
    return compare.render_html(res, f"Почему тяжёлые: {res['snapshot']}")


def write(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "hotspots.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, "hotspots.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_html(res))
    return os.path.join(out_dir, "hotspots.html")
