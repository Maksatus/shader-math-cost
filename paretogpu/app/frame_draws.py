"""Shader threads and loop trip counts of a frame's draws from the snapshot's rd_draws.json (+ .bin), measured when the
snapshot was taken by one replay walk over the capture's draws (adapters/renderdoc/rd_draws.py, on its device):
- shader threads of every draw that ran a fragment shader (ps_invocations not 0): its post-VS triangles rasterized by
  core/quads, the same on a PC and a device frame -> ev["rd"]["threads"] (priced, core/pixels), ["samples_model"];
- trip counts of dynamic loops whose bound is no shader parameter (cluster-mask scans, ray steps): n = mean over the
  pixels the draw's calls wrote."""
import json
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.adapters.renderdoc import rd_spirv
from paretogpu.core import loops, quads
from paretogpu.model.variant import VariantKey
from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

FILE = "rd_draws.json"
QUADS = "quads/10"
ACTIONS = "rd_actions.json"
MISMATCH = 0.02  # share of a draw's samples; and at least MISMATCH_MIN samples
MISMATCH_MIN = 1000


class LoopCountError(rdoc.RenderDocError):
    code = "loops"


def _unknown(ev, fn, state):
    """Whether the file has an outer loop whose trip count the event's params do not give."""
    profile = next(iter((state.get("loops") or {}).get(fn, {}).values()), None)
    if not profile:
        return False
    bounds = (state.get("loop_bounds") or {}).get(fn) or [None] * len(profile["loops"])
    return any(loops.bound_value(b, ev.get("params")) is None for b in bounds)


def run(frame_dir, events, state, rep=CONSOLE, memo=None, root=None):
    """-> (loop iterations {event index: {"n": [mean per outer loop], "pixels", "calls"}}, warnings). LoopCountError
    when a draw's loops were not counted: its price would rest on a guessed trip count."""
    looped = []
    for ev in events:
        if ev["kind"] != "draw" or not (ev.get("rd") or {}).get("events"):
            continue
        fn = (state["files"].get(VariantKey.of(ev)) or {}).get("frag")
        if fn and _unknown(ev, fn, state):
            looped.append((ev, fn))
    draws = [ev for ev in events if ev["kind"] == "draw" and (ev.get("rd") or {}).get("events")
             and ev["rd"].get("ps_invocations") != 0]
    if not looped and not draws:
        rep.skip("quads")
        return {}, []
    path = os.path.join(frame_dir, FILE)
    if not os.path.exists(path) or not os.path.exists(os.path.join(frame_dir, ACTIONS)):
        raise rdoc.RenderDocError(f"{FILE} or {ACTIONS} is missing from the snapshot: take it again")
    with open(path, encoding="utf-8") as f:
        measured = json.load(f)
    calls = sorted({e for ev in draws for e in ev["rd"]["events"]})
    discarding = _discarding(draws, state, root)
    visible = sorted({str(e) for ev in draws if _opaque(ev, discarding) for e in ev["rd"]["events"]}, key=int)
    memo = memo or memo_store.default()
    qkey = f"{rdoc.signature(path + '.bin')}|{','.join(map(str, calls))}|{','.join(visible)}"
    counted = memo.get(QUADS, qkey) if calls else {}
    if counted is None:
        rep.phase("quads")
        with open(os.path.join(frame_dir, ACTIONS), encoding="utf-8") as f:
            actions = json.load(f)["actions"]
        with open(path + ".bin", "rb") as f:
            # every call that rasterizes: depth-only and unmatched calls fill the depth the priced ones are tested against
            every = sorted(set(calls) | {int(e) for e in measured["geometry"]})
            meta, tris = quads.triangles({str(e): measured["geometry"].get(str(e)) or {"error": "not read"}
                                          for e in every}, f.read())
        rep.log(f"rasterizing {len(tris)} triangles of {len(every)} draw calls ...")
        counted = quads.count(meta, tris, actions, set(visible))
        memo.put_many(QUADS, [(qkey, counted)])
    else:
        rep.skip("quads")
    return (_iterations(looped, measured["loops"], state, rep),
            _threads(draws, counted, rep, discarding))


def _opaque(ev, discarding):
    """Opaque for Mali's Forward Pixel Kill: no blending, writes and tests depth, no discard (core/quads)."""
    return ev.get("blend") == "One Zero" and ev.get("zwrite") and ev.get("ztest") and ev["index"] not in discarding


def _iterations(looped, counts, state, rep):
    out, failed = {}, []
    for ev, fn in looped:
        priced = next(iter(state["loops"][fn].values()))["loops"]
        nested = [lp.get("word") is not None for lp in priced]
        got = [counts.get(str(e)) or {"error": "not counted when the snapshot was taken"} for e in ev["rd"]["events"]]
        bad = next((g for g in got if "error" in g or g["loops"] != len(priced) or g["nested"] != nested), None)
        if bad and "error" in bad:
            failed.append(f"event {ev['index']}: {bad['error']}")
        elif bad:
            failed.append(f"event {ev['index']}: its shader has {bad['loops']} outer loops, the priced variant "
                          f"{len(priced)}")
        elif sum(g["pixels"] for g in got):
            px = sum(g["pixels"] for g in got)
            out[ev["index"]] = {"n": [round(sum(g["sums"][k] for g in got) / px, 3) for k in range(len(priced))],
                                "pixels": px, "calls": len(got)}
    if failed:
        raise LoopCountError(f"loop trip counts of {len(failed)} draws not counted: " + "; ".join(failed[:5]))
    if looped:
        rep.log(f"  loop iterations of {len(out)} of {len(looped)} draws from the capture"
                + (f" ({len(looped) - len(out)} wrote no pixel)" if len(out) < len(looped) else ""))
    return out


def _discarding(draws, state, root):
    """Indices of the draws whose fragment shader discards: Samples Passed counts the samples that survived it."""
    seen, out = {}, set()
    for ev in draws:
        fn = (state["files"].get(VariantKey.of(ev)) or {}).get("frag")
        if fn and root and fn not in seen:
            try:
                with open(os.path.join(root, fn), "rb") as f:
                    seen[fn] = rd_spirv.discards(f.read())
            except (OSError, ValueError):
                seen[fn] = False
        if seen.get(fn):
            out.add(ev["index"])
    return out


def _threads(draws, counted, rep, discarding=()):
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
        if real is None or ev["index"] in discarding and model >= real:
            continue
        if abs(model - real) > max(MISMATCH_MIN, MISMATCH * real):
            odd.append((abs(model - real), ev["index"], model, real))
    if odd:
        odd.sort(reverse=True)
        warnings.append({"code": "quads_samples_mismatch", "n": len(odd),
                         "worst": ", ".join(f"#{i} {m:,} vs {r:,}" for _, i, m, r in odd[:3])})
    if draws:
        rep.log(f"  shader threads of {sum(1 for ev in draws if 'threads' in ev['rd'])} of {len(draws)} draws from "
                f"their quads; samples off the capture's count in {len(odd)}")
    return warnings
