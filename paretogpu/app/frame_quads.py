"""Fragment shader threads of every draw from its rasterized geometry (core/quads), the same on a PC and a device frame:
the post-VS triangles read from the capture (adapters/renderdoc/rd_geometry.py), replayed where the snapshot says. A
draw that ran no fragment shader (ps_invocations 0) has none.
Sets ev["rd"]["threads"] (priced, core/pixels) and ev["rd"]["samples_model"]; -> warnings where the model's samples
differ from the capture's Samples Passed (stencil, discard or a depth the frame loaded from elsewhere)."""
import json
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import quads
from paretogpu.store import memo as memo_store
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE

MEMO = "quads/5"
ACTIONS = "rd_actions.json"
MISMATCH = 0.02  # share of a draw's samples; and at least MISMATCH_MIN samples
MISMATCH_MIN = 1000


def threads_of(frame_dir, frame, events, rep=CONSOLE, memo=None):
    capture, serial = workspace.capture_of(frame_dir, frame)
    draws = [ev for ev in events if ev["kind"] == "draw" and (ev.get("rd") or {}).get("events")
             and ev["rd"].get("ps_invocations") != 0]
    if not draws:
        rep.skip("rd_geometry")
        return []
    if not capture or not os.path.exists(capture) or not os.path.exists(os.path.join(frame_dir, ACTIONS)):
        raise rdoc.RenderDocError(f"the capture {capture} or {ACTIONS} is missing: shader threads cannot be counted")
    with open(os.path.join(frame_dir, ACTIONS), encoding="utf-8") as f:
        actions = json.load(f)["actions"]
    instances = {a["event"]: a.get("instances") or 1 for a in actions}
    calls = sorted({e for ev in draws for e in ev["rd"]["events"]})
    memo = memo or memo_store.default()
    key = f"{rdoc.signature(capture)}|{','.join(map(str, calls))}"
    counted = memo.get(MEMO, key)
    if counted is None:
        try:
            import numpy as np
        except ImportError:
            raise rdoc.RenderDocError("counting shader threads needs numpy: pip install numpy") from None
        rep.phase("rd_geometry")
        rep.log(f"reading the triangles of {len(calls)} draw calls in RenderDoc{f' on {serial}' if serial else ''} ...")
        out = os.path.join(frame_dir, "rd_geometry.json")
        meta, blob = rdoc.geometry(capture, [[e, instances.get(e, 1)] for e in calls], out, serial,
                                   on_progress=lambda t: rep.log(f"  {t}"))
        rep.log(f"rasterizing {sum(m.get('tris', 0) for m in meta.values())} triangles ...")
        counted = quads.count(meta, np.fromfile(blob, dtype=np.float32).reshape(-1, 3, 4), actions)
        os.remove(blob)
        memo.put_many(MEMO, [(key, counted)])
    warnings, odd = [], []
    for ev in draws:
        got = [counted.get(str(e)) or {"error": "not read"} for e in ev["rd"]["events"]]
        errors = [g["error"] for g in got if "error" in g]
        if errors:
            warnings.append({"code": "quads_unread", "event": ev["index"], "error": errors[0]})
            continue
        ev["rd"]["threads"] = 4 * sum(g["quads"] for g in got)
        model = ev["rd"]["samples_model"] = sum(g["samples"] for g in got)
        real = ev["rd"].get("samples")
        if real is not None and abs(model - real) > max(MISMATCH_MIN, MISMATCH * real):
            odd.append((abs(model - real), ev["index"], model, real))
    if odd:
        odd.sort(reverse=True)
        warnings.append({"code": "quads_samples_mismatch", "n": len(odd),
                         "worst": ", ".join(f"#{i} {m:,} vs {r:,}" for _, i, m, r in odd[:3])})
    rep.log(f"  shader threads of {sum(1 for ev in draws if 'threads' in ev['rd'])} of {len(draws)} draws from their "
            f"quads; samples off the capture's count in {len(odd)}")
    return warnings
