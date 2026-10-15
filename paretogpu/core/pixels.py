"""Draw pixels = fragment shader threads: rd["threads"] from the draw's quads (app/frame_quads, PC and device alike);
without them rd["ps_invocations"]: RenderDoc PSInvocations, or Samples Passed in a device frame (core/device); 0 for a
draw that ran no fragment shader."""
from paretogpu.model.frame import Event

MISMATCH_NOTE = ("the RenderDoc calls do not match the Frame Debugger event ({what} {fd} vs {rd}): the pixels and "
                 "vertices may be another draw's")


def resolve(events: list[Event]) -> dict:
    out = {}
    for ev in events:
        if ev["kind"] != "draw":
            continue
        rd = ev.get("rd") or {}
        r = {"pixels": rd.get("ps_invocations"), "method": rd.get("pixel_method") or "renderdoc", "note": None}
        if r["pixels"] is None:
            r.update(pixels=0, method="none", note="no pixel count")
        elif rd.get("threads") is not None:
            r.update(pixels=rd["threads"], method="quads")
        elif rd.get("count_mismatch"):
            fd, got = rd["count_mismatch"]
            r["note"] = MISMATCH_NOTE.format(what=rd.get("mismatch") or "indices", fd=fd, rd=got)
        out[ev["index"]] = r
    return out
