"""The measured variant of a frame event (the state of app/variants.run())."""
from paretogpu.model.variant import API, STAGE, key_of, stages_of


def match(ev, state, api="vulkan"):
    """(records, reason) for one event: records = {"fragment": {core: rec}, "vertex": {core: rec}}
    ({"compute": {core: rec}} for a dispatch) with the measurement records of the variant in `api`;
    reason (None if every stage is measured) says what is missing."""
    if ev["kind"] not in ("draw", "compute"):
        return None, None
    k = key_of(ev)
    plat = {v: p for p, v in API.items()}[api]
    files, errs = state["files"].get(k, {}), state["errors"].get(k, [])
    out, why = {}, []
    for s in stages_of(k):
        stage = STAGE[s]
        fn = files.get((plat, s))
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
