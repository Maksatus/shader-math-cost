"""Pixels and vertices of every draw event for the frame cost (plan items K1.3 rule, K1.5).

Source of the fragment count, best first (decision in K1.3):
  renderdoc   PSInvocations of the event's calls (ev["rd"], adapters/renderdoc): how many times the pixel
              shader really ran, with overdraw, alpha test and the draw order — the reference;
  fullscreen  a full-screen pass (procedural triangle / quad in post): the render target area;
  diff        changed pixels of the render target (Frame Debugger replay, K1.2) — the fallback.
Vertices: Frame Debugger m_VertexCount (all instances included).
"""


def resolve(events):
    """{event index: {"pixels", "method", "low", "high", "note"}} for every draw event.
    low / high bound the fragment count where the method is an estimate (None when unknown)."""
    out = {}
    for ev in events:
        if ev["kind"] != "draw":
            continue
        rd = ev.get("rd") or {}
        diff, method = ev.get("pixel_count"), ev.get("pixel_method")
        w, h = ev["rt"]["width"] or 0, ev["rt"]["height"] or 0
        r = {"pixels": diff, "method": method or "unknown", "low": None, "high": None, "note": None}
        if rd.get("ps_invocations") is not None:
            r.update(pixels=rd["ps_invocations"], method="renderdoc")
        elif method == "renderdoc":
            pass
        elif method == "fullscreen":
            r.update(pixels=w * h, high=w * h)
        if r["pixels"] is None:
            r.update(pixels=0, method="none", note="no pixel count")
        out[ev["index"]] = r
    return out
