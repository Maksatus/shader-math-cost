"""How many times the dynamic loops of a frame's draws really ran (plan K1.10, stage C): `cost` with --loop-pixels.

A loop whose bound is not a shader parameter (a scan of a cluster mask: lights and probes per pixel; ray steps) has
no trip count in the snapshot. The RenderDoc capture of the snapshot (frame.rdc) has it: for every draw that still
has such a loop, a few pixels of its calls are debugged in RenderDoc (adapters/renderdoc/rd_loops.py) and the
iterations of every outer loop of its pixel shader counted. The pixels are spread over the calls of the event by
their PSInvocations, and so are the means. Remembered per capture and call (store/memo.py).

  iterations_of(frame_dir, frame, state, api, pixels) -> {event index: {"n": [mean per outer loop], "pixels", "calls"}}
"""
import json
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import loops
from paretogpu.model.variant import PLATFORM, VariantKey
from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

MEMO = "rd_loops/1"
CAPTURE = "frame.rdc"
ACTIONS = "rd_actions.json"


def _unknown(ev, fn, state):
    """Whether a file of the event has an outer loop whose trip count the event's parameters do not give."""
    profile = next(iter((state.get("loops") or {}).get(fn, {}).values()), None)
    if not profile:
        return False
    bounds = (state.get("loop_bounds") or {}).get(fn) or [None] * len(profile["loops"])
    return any(loops.bound_value(b, ev.get("params")) is None for b in bounds)


def _split(total, calls):
    """[(call, pixels)]: `total` pixels over the calls by their pixel shader runs (largest remainder): a batch of many
    small calls gets its pixels in the large ones, a call with none is not debugged."""
    runs = sum(ps for _, ps in calls)
    if not runs:
        return []
    share = [(eid, total * ps / runs) for eid, ps in calls if ps]
    n = {eid: int(s) for eid, s in share}
    for eid, s in sorted(share, key=lambda x: -(x[1] - int(x[1])))[:total - sum(n.values())]:
        n[eid] += 1
    return [(eid, k) for eid, k in n.items() if k]


def iterations_of(frame_dir, frame, state, api="vulkan", pixels=16, rep=CONSOLE, memo=None):
    """{event index: {"n": [mean iterations per outer loop], "pixels": debugged, "calls": calls used}} of the draws
    with a loop of unknown trip count; {} without a capture or with pixels = 0."""
    capture = os.path.join(frame_dir, CAPTURE)
    platform = PLATFORM[api]
    want = []
    for ev in frame["events"]:
        if ev["kind"] != "draw" or not (ev.get("rd") or {}).get("events"):
            continue
        fn = (state["files"].get(VariantKey.of(ev)) or {}).get((platform, "frag"))
        if fn and _unknown(ev, fn, state):
            want.append((ev, fn))
    if not want or pixels <= 0 or not os.path.exists(capture) or not os.path.exists(os.path.join(frame_dir, ACTIONS)):
        rep.skip("rd_loops")
        return {}
    rep.phase("rd_loops")
    with open(os.path.join(frame_dir, ACTIONS), encoding="utf-8") as f:
        ps = {a["event"]: a.get("ps_invocations") or 0 for a in json.load(f)["actions"]}
    memo = memo or memo_store.default()
    sig = rdoc.signature(capture)
    plan = {ev["index"]: _split(pixels, [(e, ps.get(e, 0)) for e in ev["rd"]["events"]]) for ev, _ in want}
    asked = {(eid, n) for calls in plan.values() for eid, n in calls}
    got = {k: memo.get(MEMO, f"{sig}|{k[0]}|{k[1]}") for k in asked}
    missing = sorted(k for k, v in got.items() if v is None)
    if missing:
        rep.log(f"counting the loop iterations of {len(want)} draws in RenderDoc: {sum(n for _, n in missing)} pixels "
                f"of {len(missing)} calls ...")
        res = rdoc.loop_iterations(capture, [{"eid": e, "pixels": n} for e, n in missing],
                                   os.path.join(frame_dir, "rd_loops.json"))
        for k in missing:
            got[k] = res.get(k[0]) or {"error": "not counted"}
        memo.put_many(MEMO, [(f"{sig}|{k[0]}|{k[1]}", got[k]) for k in missing if "error" not in got[k]])
    out = {}
    for ev, fn in want:
        size = len(next(iter(state["loops"][fn].values()))["loops"])
        acc, weight, n_px, used = [0.0] * size, 0, 0, 0
        for eid, n in plan[ev["index"]]:
            r = got.get((eid, n)) or {}
            s = r.get("samples") or []
            if not s or len(r.get("loops") or []) != size:
                continue  # another number of outer loops than the priced shader: not this variant's loops
            w = ps.get(eid, 0)
            for k in range(size):
                acc[k] += w * sum(x[k] for x in s) / len(s)
            weight, n_px, used = weight + w, n_px + len(s), used + 1
        if weight:
            out[ev["index"]] = {"n": [round(a / weight, 3) for a in acc], "pixels": n_px, "calls": used}
    rep.log(f"  loop iterations of {len(out)} of {len(want)} draws from the capture")
    return out
