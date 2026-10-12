"""The cost of a frame snapshot (plan items K1.4–K1.9, K1.13): what `python -m paretogpu cost` runs (features/cost.py).

Every event's exact shader variant is compiled (app/variants.py: checked against the shaders in the open editor) and
measured with malioc, dynamic loops are priced at n iterations, and the frame is priced (core/frame_cost.py).
materials=True adds the shader variants of every material of the project ("project shaders" tab).

  run(frame_dir, root, cores, ...) -> (CostRun, VariantState): the complete document of frame_cost.json and the
  variants it was priced from
"""
import json
import os
import time

from paretogpu.adapters.renderdoc import RenderDocError
from paretogpu.app import frame_loops
from paretogpu.app import materials as project_materials
from paretogpu.app import variants
from paretogpu.core import frame_cost
from paretogpu.core import materials as shaders
from paretogpu.core import profile
from paretogpu.core.pricing import LOOP_ITERS
from paretogpu.model.cores import main_core as main_core_of
from paretogpu.model.cost import CostRun
from paretogpu.model.variant import PLATFORM, VariantState
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


def run(frame_dir, root, cores, api="vulkan", main_core=None, project=None, materials=False, compile_missing=True,
        recompile=False, retry_failed=False, jobs=None, loop_iters=LOOP_ITERS, loop_overrides=None, loop_pixels=16,
        rep=CONSOLE) -> tuple[CostRun, VariantState]:
    """Price the snapshot in frame_dir on the cores: its variants compiled into root and measured, the frame priced
    with the dynamic loops at loop_iters iterations (loop_overrides: {part of a shader name: n}). project: the Unity
    project to compile in (default: the snapshot's). loop_pixels: pixels of every draw debugged in the snapshot's
    RenderDoc capture to count the iterations of its loops of unknown trip count (app/frame_loops.py; 0: none). Returns the document of frame_cost.json with everything the
    report and the lists read, and the state of the variants (app/variants.run) it was priced from."""
    with open(os.path.join(frame_dir, workspace.SNAPSHOT_FILE), encoding="utf-8") as f:
        frame = json.load(f)
    events = frame["events"]
    project = project or frame.get("project")
    main_core = main_core or main_core_of(cores)
    mat_keys = mat_skipped = None
    if materials:
        rep.phase("materials")
        mat_keys, mat_skipped = project_materials.project_variants(
            project, events, [os.path.dirname(os.path.abspath(frame_dir)), workspace.OUT], root,
            compile_missing=compile_missing, rep=rep)
    state = variants.run(events + ([k.as_event() for k in mat_keys] if mat_keys else []), project, root, cores,
                         [PLATFORM[api]], jobs, compile_missing=compile_missing, rep=rep, recompile=recompile,
                         retry_failed=retry_failed)
    state["loops"] = variants.loops_of(state, root, cores, jobs, rep)
    state["loop_bounds"] = variants.bounds_of(state, project, root, compile_missing, rep)
    try:
        state["loop_iters"] = frame_loops.iterations_of(frame_dir, frame, state, api, loop_pixels, rep)
    except RenderDocError as e:
        rep.log(f"  loop iterations not counted: {e}")
        state["loop_iters"] = {}
    rep.phase("report")
    c = frame_cost.compute(frame, events, state, api, cores, main_core, loop_iters=loop_iters,
                           loop_overrides=loop_overrides)
    c["variants_checked"] = state["checked"]
    prof = profile.describe(frame, profile.frame_size(c["events"]))
    c["profile"] = {**prof, "warnings": profile.warnings(prof)}
    if mat_keys is not None:
        c["project_shaders"] = shaders.rows(mat_keys, mat_skipped, state, c, api, loop_iters, loop_overrides)
    recs = [r for f in state["measurements"].values() for r in f.values() if r.get("ok", True)]
    c["malioc"] = ", ".join(sorted({r["malioc"] for r in recs}))
    c["frame_dir"] = os.path.abspath(frame_dir)
    c["variants_dir"] = os.path.abspath(root)
    c["computed_at"] = time.time()
    return c, state
