"""Trip counts of dynamic loops whose bound is not a shader parameter (cluster-mask scans, ray steps): a few pixels per
draw debugged in RenderDoc, sampled over its calls weighted by PSInvocations; n = mean over pixels."""
import json
import os
import random
from collections import Counter

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import loops
from paretogpu.model.variant import PLATFORM, VariantKey
from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

MEMO = "rd_loops/2"
CAPTURE = "frame.rdc"
ACTIONS = "rd_actions.json"


def _unknown(ev, fn, state):
    """Whether the file has an outer loop whose trip count the event's params do not give."""
    profile = next(iter((state.get("loops") or {}).get(fn, {}).values()), None)
    if not profile:
        return False
    bounds = (state.get("loop_bounds") or {}).get(fn) or [None] * len(profile["loops"])
    return any(loops.bound_value(b, ev.get("params")) is None for b in bounds)


def _split(total, calls, seed=0):
    """-> [(call, pixels)], deterministic per seed; calls with 0 picks are omitted."""
    calls = [(eid, ps) for eid, ps in calls if ps]
    if not calls:
        return []
    picks = Counter(random.Random(seed).choices([eid for eid, _ in calls], weights=[ps for _, ps in calls], k=total))
    return [(eid, picks[eid]) for eid, _ in calls if picks[eid]]


def iterations_of(frame_dir, frame, state, api="vulkan", pixels=16, rep=CONSOLE, memo=None):
    """-> {event index: {"n": [mean per outer loop], "pixels", "calls"}}; {} without a capture or with pixels = 0."""
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
    plan = {ev["index"]: _split(pixels, [(e, ps.get(e, 0)) for e in ev["rd"]["events"]], ev["index"]) for ev, _ in want}
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
        memo.put_many(MEMO, [(f"{sig}|{k[0]}|{k[1]}", got[k]) for k in missing
                             if "error" not in got[k] and (got[k].get("samples") or not got[k].get("covered"))])
    out = {}
    for ev, fn in want:
        priced = next(iter(state["loops"][fn].values()))["loops"]
        size, nested = len(priced), [lp.get("word") is not None for lp in priced]
        acc, n_px, used = [0.0] * size, 0, 0
        for eid, n in plan[ev["index"]]:
            r = got.get((eid, n)) or {}
            s = r.get("samples") or []
            if not s or len(r.get("loops") or []) != size or r.get("nested", nested) != nested:
                continue  # outer loops differ from the priced file's: not this variant
            for k in range(size):
                acc[k] += sum(x[k] for x in s)
            n_px, used = n_px + len(s), used + 1
        if n_px:
            out[ev["index"]] = {"n": [round(a / n_px, 3) for a in acc], "pixels": n_px, "calls": used}
    rep.log(f"  loop iterations of {len(out)} of {len(want)} draws from the capture")
    return out
