"""The cost of a frame (frame_cost.json, core/frame_cost.compute): every event priced on every core, and its short
account (core/frame_cost.summary) for the lists of runs, the comparison and the UI."""
from typing import TypedDict


class StageCosts(TypedDict, total=False):
    """One event on one core: the prices (cycles per pixel / vertex / thread) and the cycles they add up to."""
    px_price: float
    vtx_price: float
    cs_price: float
    px_bound: list
    vtx_bound: list
    cs_bound: list
    px_path: str
    vtx_path: str
    cs_path: str
    fragment: float   # pixels x px_price
    vertex: float     # vertices x vtx_price
    compute: float    # threads x cs_price
    total: float
    share: float      # of the frame on that core
    flags: list
    px_regs: int
    vtx_regs: int
    px_fp16: int
    total_by_n: dict      # {n: total} with the dynamic loops at n iterations
    px_loops: list        # core/pricing.loop_parts of the fragment stage: [{"n", "cycles"}] per outer loop
    cs_loops: list        # the same of a compute kernel
    px_price_by_n: dict


# one event of the frame (draw or dispatch) with its cost per core
EventCost = TypedDict("EventCost", {
    "index": int, "kind": str, "stage": str, "object": str, "path": str, "meshes": list, "shader": str,
    "pass": str, "keywords": list, "rt": str, "rt_size": list, "pixels": int, "pixel_method": str,
    "pixels_low": int, "pixels_high": int, "pixel_note": str, "vertices": int, "vertex_method": str,
    "threads": int, "thread_method": str, "kernel": str, "variant": str, "files": dict,
    "cost": dict,      # {core: StageCosts}
    "reason": str,     # why there is no price
    "loop": dict,      # {"n", "fixed"} when its dynamic loops are priced at n
}, total=False)


class CostRun(TypedDict, total=False):
    """frame_cost.json and every costs/<time>.json (store/cost_runs.py)."""
    frame: dict           # the snapshot's meta (model/frame.Snapshot without the events)
    profile: dict         # core/profile.describe of the snapshot and the frame size + "warnings": [{"code", ...}]
    api: str
    cores: list           # [{"name", "arch"}]
    main_core: str
    totals: dict          # {core: {"total", "fragment", "vertex", "compute"}}
    loops: dict           # {"n", "overrides", "range", "events", "files", "unforced", "by_n"}
    coverage: dict
    pixel_methods: dict
    vertex_methods: dict
    groups: dict          # {core: {group: [{key, events, pixels, ..., total, share}]}}
    missing: list
    events: list          # [EventCost]
    variants_checked: bool
    malioc: str
    frame_dir: str
    variants_dir: str
    computed_at: float
    project_shaders: dict


class CostSummary(TypedDict, total=False):
    """core/frame_cost.summary of a CostRun: where it is from (snapshot, frame_dir, project, computed_at), what it was
    priced with (api, malioc, main_core, cores, loops_n, checked), the frame (resolution, quality, pipeline, scale,
    renderdoc), its cycles on the main core (total, fragment, vertex, compute) and on every core (totals), what was
    priced (coverage, priced, pixel_methods), the costliest shaders and what has no price (unpriced_draws,
    unpriced_compute, failed_reasons)."""
    snapshot: str
    frame_dir: str
    project: str
    computed_at: float
    api: str
    malioc: str
    main_core: str
    cores: list
    loops_n: int
    checked: bool
    resolution: list
    quality: str
    pipeline: str
    scale: float
    renderdoc: bool
    total: float
    fragment: float
    vertex: float
    compute: float
    totals: dict
    coverage: dict
    priced: int
    pixel_methods: dict
    shaders: list
    unpriced_draws: int
    unpriced_compute: int
    failed_reasons: list
