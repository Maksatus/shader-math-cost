"""Prices a frame snapshot (`python -m paretogpu cost`). A device frame carries the code the phone ran: measured as is,
nothing compiled. materials=True adds every project material's variants ("project shaders" tab)."""
import json
import os
import time

from paretogpu.app import code_version
from paretogpu.app import frame_draws
from paretogpu.app import frame_memory
from paretogpu.app import materials as project_materials
from paretogpu.app import variants
from paretogpu.core import device
from paretogpu.core import frame_cost
from paretogpu.core import materials as shaders
from paretogpu.core import memory
from paretogpu.core import profile
from paretogpu.core.events import assign_stages, global_keywords_known
from paretogpu.model.cores import main_core as main_core_of
from paretogpu.model.cost import CostRun
from paretogpu.model.variant import VariantKey, VariantState
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


def run(frame_dir, root, cores, project=None, materials=False, compile_missing=True,
        rep=CONSOLE) -> tuple[CostRun, VariantState]:
    with open(os.path.join(frame_dir, workspace.SNAPSHOT_FILE), encoding="utf-8") as f:
        frame = json.load(f)
    events = assign_stages(frame["events"])
    project = project or frame.get("project")
    gpu = (frame.get("device") or {}).get("gpu")
    if gpu and gpu not in cores:
        cores = [*cores, gpu]
    main_core = gpu or main_core_of(cores)
    mat_keys = mat_skipped = None
    if materials:
        rep.phase("materials")
        own = events if global_keywords_known(events) else None
        mat_keys, mat_skipped = project_materials.project_variants(
            project, own, [os.path.dirname(os.path.abspath(frame_dir)), workspace.OUT], root,
            compile_missing=compile_missing, rep=rep)
    if variants.identify(events, frame_dir, root, project, rep):
        events = assign_stages(events)  # the identified passes tell the stages
    coded = [e for e in events if e["kind"] == "draw" and (e.get("code") or {}).get("frag")]
    unmatched = sum(1 for e in coded if not e["code"].get("variant"))
    captured = variants.place_captured(events, frame_dir, root)
    state = variants.run(events + ([k.as_event() for k in mat_keys] if mat_keys else []), project, root, cores,
                         compile_missing=compile_missing, rep=rep, captured=captured)
    variants.check_pipeline(frame.get("pipeline"), state,
                            {VariantKey.of(e) for e in events if e["kind"] == "draw"} - set(captured))
    state["loops"] = variants.loops_of(state, root, cores, rep=rep)
    state["loop_bounds"] = variants.bounds_of(state, root, rep)
    if frame.get("device"):
        rep.skip("quads")
        state["loop_iters"], quad_warnings = {}, []
    else:
        state["loop_iters"], quad_warnings = frame_draws.run(frame_dir, events, state, rep, root=root)
    capture = frame_memory.read(frame_dir, frame, rep)
    rep.phase("report")
    tiles = memory.tile_threads(capture["textures"], capture["usage"], capture["actions"], events) if capture else []
    c = frame_cost.compute(frame, events, state, cores, main_core, tiles)
    c["memory"] = frame_memory.passes(capture, events, c["events"], [x["name"] for x in c["cores"]], rep)
    if capture:
        dev = frame.get("device") or {}
        core = dev.get("gpu") if dev.get("gpu") in [x["name"] for x in c["cores"]] else c["main_core"]
        c["hardware"] = device.hardware(dev, capture["actions"], events, c["events"] + c["geometry"], core)
        if frame.get("device"):
            frame_cost.measured(c, (c["hardware"] or {}).get("shader_cost"), events, core)
    c["variants_checked"] = state["checked"]
    prof = profile.describe(frame, profile.frame_size(c["events"]))
    c["profile"] = {**prof, "warnings": profile.warnings(prof),
                    "render": profile.render(c["events"], c.get("memory"), frame.get("screen"))}
    c["profile"]["warnings"] += quad_warnings
    astray = sum(1 for e in events if (e.get("rd") or {}).get("count_mismatch"))
    if astray:
        c["profile"]["warnings"].append({"code": "rd_count_mismatch", "n": astray})
    dev = frame.get("device") or {}
    if unmatched:
        c["profile"]["warnings"].append({"code": "device_unmatched", "n": unmatched, "total": len(coded)})
    fb = c.get("measured_fallback") or {}
    if fb.get("events"):
        t = (c["totals"].get(c["main_core"]) or {}).get("total") or 0.0
        c["profile"]["warnings"].append({"code": "measured_fallback", "n": fb["events"],
                                         "share": f"{fb['cycles'] / t:.0%}" if t else "?"})
    if gpu and c["main_core"] != gpu:
        c["profile"]["warnings"].append({"code": "device_core_unpriced", "gpu": gpu, "core": c["main_core"]})
    if dev.get("discard_unshaded"):
        c["profile"]["warnings"].append({"code": "device_discard_unshaded", "n": dev["discard_unshaded"],
                                         "error": dev.get("discard_error") or "?"})
    if mat_keys is not None:
        c["project_shaders"] = shaders.rows(mat_keys, mat_skipped, state, c)
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    c["malioc"] = ", ".join(sorted({r["malioc"] for r in recs}))
    c["frame_dir"] = os.path.abspath(frame_dir)
    c["variants_dir"] = os.path.abspath(root)
    c["computed_at"] = time.time()
    c["code_version"] = code_version.current()
    rep.done()
    return c, state
