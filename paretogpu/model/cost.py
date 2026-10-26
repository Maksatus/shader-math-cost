from typing import TypedDict


class StageCosts(TypedDict, total=False):
    """One event on one core; *_price in cycles per pixel / vertex / thread."""
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
    px_loops: list        # [{"n", "cycles"}] per outer loop (core/pricing.loop_parts)
    cs_loops: list        # the same for a compute kernel
    px_ref: float
    vtx_ref: float
    cs_ref: float


EventCost = TypedDict("EventCost", {
    "index": int, "kind": str, "stage": str, "object": str, "path": str, "meshes": list, "shader": str,
    "pass": str, "keywords": list, "rt": str, "rt_size": list, "pixels": int, "pixel_method": str,
    "pixel_note": str, "vertices": int, "vertex_method": str,
    "threads": int, "thread_method": str, "kernel": str, "variant": str, "files": dict,
    "cost": dict,      # {core: StageCosts}
    "reason": str,     # why there is no price
    "loop": dict,      # {"n", "loops": [{"n", "from", ...}]} when its dynamic loops are priced by trip count
}, total=False)


class CostRun(TypedDict, total=False):
    """frame_cost.json and every costs/<time>.json."""
    frame: dict           # the snapshot's meta (model/frame.Snapshot without the events)
    profile: dict         # core/profile.describe of the snapshot and the frame size + "warnings": [{"code", ...}]
    api: str
    cores: list           # [{"name", "arch"}]
    main_core: str
    totals: dict          # {core: {"total", "fragment", "vertex", "compute"}}
    loops: dict           # {"n" (default), "events", "files", "bounded", "measured", "unknown", "unforced"}
    coverage: dict
    pixel_methods: dict
    vertex_methods: dict
    missing: list
    events: list          # [EventCost]
    variants_checked: bool
    malioc: str
    frame_dir: str
    variants_dir: str
    computed_at: float
    code_version: str
    project_shaders: dict


class CostSummary(TypedDict, total=False):
    """core/frame_cost.summary of a CostRun; total/fragment/vertex/compute are on the main core."""
    snapshot: str
    frame_dir: str
    project: str
    computed_at: float
    code_version: str
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
    render: dict
    renderdoc: bool
    total: float
    fragment: float
    vertex: float
    compute: float
    totals: dict
    coverage: dict
    priced: int
    pixel_methods: dict
    vertex_methods: dict
    shaders: list
    unpriced_draws: int
    unpriced_compute: int
    failed_reasons: list
