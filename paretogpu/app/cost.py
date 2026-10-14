"""Prices a frame snapshot (`python -m paretogpu cost`). A device frame carries the code the phone ran: measured as is,
nothing compiled. materials=True adds every project material's variants ("project shaders" tab)."""
import json
import os
import time

from paretogpu.adapters.renderdoc import RenderDocError
from paretogpu.app import code_version
from paretogpu.app import frame_loops
from paretogpu.app import frame_memory
from paretogpu.app import materials as project_materials
from paretogpu.app import variants
from paretogpu.core import frame_cost
from paretogpu.core import materials as shaders
from paretogpu.core import profile
from paretogpu.core.events import assign_stages
from paretogpu.model.cores import main_core as main_core_of
from paretogpu.model.cost import CostRun
from paretogpu.model.variant import PLATFORM, VariantState
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


def run(frame_dir, root, cores, api="vulkan", project=None, materials=False, compile_missing=True, loop_pixels=16,
        rep=CONSOLE) -> tuple[CostRun, VariantState]:
    with open(os.path.join(frame_dir, workspace.SNAPSHOT_FILE), encoding="utf-8") as f:
        frame = json.load(f)
    events = assign_stages(frame["events"])
    project = project or frame.get("project")
    main_core = main_core_of(cores)
    mat_keys = mat_skipped = None
    if materials:
        rep.phase("materials")
        mat_keys, mat_skipped = project_materials.project_variants(
            project, events, [os.path.dirname(os.path.abspath(frame_dir)), workspace.OUT], root,
            compile_missing=compile_missing, rep=rep)
    variants.identify(events, frame_dir, root, rep)
    state = variants.run(events + ([k.as_event() for k in mat_keys] if mat_keys else []), project, root, cores,
                         [PLATFORM[api]], compile_missing=compile_missing, rep=rep,
                         captured=variants.place_captured(events, frame_dir, root))
    state["loops"] = variants.loops_of(state, root, cores, rep=rep)
    state["loop_bounds"] = variants.bounds_of(state, project, root, compile_missing, rep)
    try:
        state["loop_iters"] = frame_loops.iterations_of(frame_dir, frame, state, api, loop_pixels, rep)
    except RenderDocError as e:
        rep.log(f"  loop iterations not counted: {e}")
        state["loop_iters"] = {}
    try:
        capture = frame_memory.read(frame_dir, rep)
    except RenderDocError as e:
        rep.log(f"  memory of the passes not read: {e}")
        capture = None
    rep.phase("report")
    c = frame_cost.compute(frame, events, state, api, cores, main_core)
    c["memory"] = frame_memory.passes(capture, events, c["events"], [x["name"] for x in c["cores"]], rep)
    c["variants_checked"] = state["checked"]
    prof = profile.describe(frame, profile.frame_size(c["events"]))
    c["profile"] = {**prof, "warnings": profile.warnings(prof),
                    "render": profile.render(c["events"], c.get("memory"), frame.get("screen"))}
    astray = sum(1 for e in events if (e.get("rd") or {}).get("count_mismatch"))
    if astray:
        c["profile"]["warnings"].append({"code": "rd_count_mismatch", "n": astray})
    dev = frame.get("device") or {}
    if frame.get("source") == "device" and dev.get("discard_unshaded"):
        c["profile"]["warnings"].append({"code": "device_discard_unshaded", "n": dev["discard_unshaded"],
                                         "error": dev.get("discard_error") or "?"})
    if mat_keys is not None:
        c["project_shaders"] = shaders.rows(mat_keys, mat_skipped, state, c, api)
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    c["malioc"] = ", ".join(sorted({r["malioc"] for r in recs}))
    c["frame_dir"] = os.path.abspath(frame_dir)
    c["variants_dir"] = os.path.abspath(root)
    c["computed_at"] = time.time()
    c["code_version"] = code_version.current()
    return c, state
