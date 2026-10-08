"""The cost of a frame (frame_cost.json, core/frame_cost.compute): every event priced on every core."""
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
