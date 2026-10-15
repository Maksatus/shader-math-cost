"""Trip counts of dynamic loops whose bound is not a shader parameter (cluster-mask scans, ray steps): every call of the
draw replayed with a fragment shader that writes its loops' trip counts as its colour (rd_loops.py), over all its
pixels; the capture replays where the snapshot says (its device). n = mean over the pixels of the draw's calls."""
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import loops
from paretogpu.model.variant import VariantKey
from paretogpu.store import memo as memo_store
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE

MEMO = "rd_loops/6"


class LoopCountError(rdoc.RenderDocError):
    code = "loops"


def _unknown(ev, fn, state):
    """Whether the file has an outer loop whose trip count the event's params do not give."""
    profile = next(iter((state.get("loops") or {}).get(fn, {}).values()), None)
    if not profile:
        return False
    bounds = (state.get("loop_bounds") or {}).get(fn) or [None] * len(profile["loops"])
    return any(loops.bound_value(b, ev.get("params")) is None for b in bounds)


def iterations_of(frame_dir, frame, state, rep=CONSOLE, memo=None):
    """-> {event index: {"n": [mean per outer loop], "pixels", "calls"}}. LoopCountError when a draw's loops cannot be
    counted: its price would rest on a guessed trip count."""
    capture, serial = workspace.capture_of(frame_dir, frame)
    want = []
    for ev in frame["events"]:
        if ev["kind"] != "draw" or not (ev.get("rd") or {}).get("events"):
            continue
        fn = (state["files"].get(VariantKey.of(ev)) or {}).get("frag")
        if fn and _unknown(ev, fn, state):
            want.append((ev, fn))
    if not want:
        rep.skip("rd_loops")
        return {}
    if not capture or not os.path.exists(capture):
        raise LoopCountError(f"{len(want)} draws have loops of unknown trip count and the capture {capture} is missing")
    rep.phase("rd_loops")
    memo = memo or memo_store.default()
    sig = rdoc.signature(capture)
    keys = {ev["index"]: ",".join(map(str, sorted(ev["rd"]["events"]))) for ev, _ in want}
    got = {k: memo.get(MEMO, f"{sig}|{k}") for k in keys.values()}
    missing = [k for k, v in got.items() if v is None]
    if missing:
        rep.log(f"counting the loop iterations of {len(missing)} draws in RenderDoc"
                f"{f' on {serial}' if serial else ''} ...")
        res = rdoc.loop_iterations(capture, [{"key": k, "calls": [int(e) for e in k.split(",")]} for k in missing],
                                   os.path.join(frame_dir, "rd_loops.json"), serial)
        for k in missing:
            got[k] = res.get(k) or {"error": "not counted"}
        if res.get("_seconds"):
            rep.log("  seconds: " + ", ".join(f"{k} {v:g}" for k, v in sorted(res["_seconds"].items())))
        memo.put_many(MEMO, [(f"{sig}|{k}", got[k]) for k in missing if "error" not in got[k]])
    out, failed = {}, []
    for ev, fn in want:
        priced = next(iter(state["loops"][fn].values()))["loops"]
        nested = [lp.get("word") is not None for lp in priced]
        r = got[keys[ev["index"]]]
        if "error" in r:
            failed.append(f"event {ev['index']}: {r['error']}")
        elif r["loops"] != len(priced) or r["nested"] != nested:
            failed.append(f"event {ev['index']}: its shader has {r['loops']} outer loops, the priced variant "
                          f"{len(priced)}")
        elif r["pixels"]:
            out[ev["index"]] = {"n": [round(m, 3) for m in r["means"]], "pixels": r["pixels"], "lost": r["lost"],
                                "calls": len(ev["rd"]["events"])}
    if failed:
        raise LoopCountError(f"loop trip counts of {len(failed)} draws not counted: " + "; ".join(failed[:5]))
    rep.log(f"  loop iterations of {len(out)} of {len(want)} draws from the capture"
            + (f" ({len(want) - len(out)} wrote no pixel)" if len(out) < len(want) else ""))
    return out
