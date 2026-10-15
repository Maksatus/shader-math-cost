from paretogpu.model.frame import Event
from paretogpu.model.variant import STAGE, VariantKey, VariantState


def match(ev: Event, state: VariantState):
    """-> ({stage: {core: Measurement}}, reason or None). Stages: fragment + vertex, compute, or vertex alone for a
    device depth-only draw."""
    if ev["kind"] not in ("draw", "compute"):
        return None, None
    k = VariantKey.of(ev)
    files, errs = state["files"].get(k, {}), state["errors"].get(k, [])
    out, why = {}, []
    # a captured (device) depth-only draw has no fragment stage
    captured = k in (state.get("captured") or ())
    stages = [s for s in k.stages if not (captured and s == "frag" and files and s not in files)]
    for s in stages:
        stage = STAGE[s]
        fn = files.get(s)
        if not fn:
            why.append(f"{stage}: not exported" + (f" ({'; '.join(errs)})" if errs else ""))
            continue
        recs = state["measurements"].get(fn, {})
        ok = {c: r for c, r in recs.items() if r.get("ok", True)}
        if not ok:
            bad = next((r.get("error", "") for r in recs.values()), "not measured")
            why.append(f"{stage}: malioc failed: {bad.strip().splitlines()[-1] if bad.strip() else bad}")
            continue
        out[stage] = ok
    return out, ("; ".join(why) or None)
