"""A frame snapshot (frame_events.json): the events of the Frame Debugger with their RenderDoc counters."""
from typing import TypedDict

RenderTarget = TypedDict("RenderTarget", {"name": str, "width": int, "height": int, "count": int,
                                          "backbuffer": bool}, total=False)

Compute = TypedDict("Compute", {"shader": str, "kernel": str, "groups": list, "group_size": list}, total=False)

# the event's RenderDoc calls (core/events.match); a counter the capture does not have is None, not 0
RdCounters = TypedDict("RdCounters", {
    "calls": int, "events": list, "ps": int, "vs": int, "ps_invocations": int, "vs_invocations": int,
    "cs_invocations": int, "samples": int, "gpu_ms": float, "count_mismatch": list}, total=False)

# one Frame Debugger event (core/events.normalize); kind: draw | compute | other;
# stage: shadow | prepass | opaque | transparent | post | ui | compute | other
Event = TypedDict("Event", {
    "index": int, "type": str, "path": str, "kind": str, "object": str, "mesh": str, "meshes": list,
    "pixels": int, "shader": str, "original_shader": str, "pass": str, "light_mode": str, "subshader": int,
    "pass_index": int, "keywords": list, "global_keywords": list, "vertices": int, "indices": int,
    "instances": int, "draw_calls": int, "rt": RenderTarget, "blend": str, "write_mask": int, "ztest": int,
    "zwrite": bool, "cull": int, "batch_break": int, "compute": Compute, "stage": str, "pixel_count": int,
    "pixel_method": str, "rd": RdCounters}, total=False)


class Snapshot(TypedDict, total=False):
    """frame_events.json: what the editor said about the frame, and its events."""
    project: str
    unity: str
    graphics_api: str
    quality: str
    play_mode: bool
    seconds: float
    renderdoc: dict   # capture, matched, missing, mismatched, counters, attempts
    events: list      # [Event]
