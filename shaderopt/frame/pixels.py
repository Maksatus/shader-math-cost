"""Pixels and vertices of every draw event for the frame cost (plan items K1.3 rule, K1.5).

Source of the fragment count, best first (decision in K1.3):
  renderdoc   PSInvocations of the event's calls (ev["rd"], frame/renderdoc.py): how many times the pixel
              shader really ran, with overdraw, alpha test and the draw order — the reference;
  fullscreen  a full-screen pass (procedural triangle / quad in post): the render target area;
  frustum     the event's renderers in frustum.json (frame/frustum.py), when the snapshot has no RenderDoc:
                opaque       max(changed pixels, raster): an opaque draw is shaded on almost all its raster
                             (drawn early, covered later — still shaded). Alpha-tested draws stay low
                             (frustum.json counts after `clip`);
                transparent  changed pixels, raster as the upper bound (frustum visible / raster overcount
                             transparent draws);
  diff        changed pixels of the render target (Frame Debugger replay, K1.2) — the fallback.
Vertices: Frame Debugger m_VertexCount (all instances included).
"""
from collections import defaultdict

COLOR_STAGES = ("opaque", "transparent")


def frustum_match(events, items):
    """Assign frustum items (renderer x submesh x material) to the color draws that drew them:
    {event index: [items]}. Batched meshes of an event (ev["meshes"]) are taken in order, each from the
    first unassigned item with the same shader and mesh; items Unity drew in the frame and with more keywords
    in common come first."""
    pool = defaultdict(list)
    for it in items:
        pool[(it["shader"], it["mesh"])].append(it)
    used, out = set(), {}
    for ev in events:
        if ev["kind"] != "draw" or ev["stage"] not in COLOR_STAGES:
            continue
        kws = set(ev["keywords"])
        got = []
        for mesh in ev["meshes"] or ([ev["mesh"]] if ev["mesh"] else []):
            cands = [i for i in pool.get((ev["shader"], mesh), []) if id(i) not in used]
            if not cands:
                continue
            best = max(cands, key=lambda i: (i["rendered"], len(kws & set(i["keywords"]))))
            used.add(id(best))
            got.append(best)
        if got:
            out[ev["index"]] = got
    return out


def resolve(events, frustum=None):
    """{event index: {"pixels", "method", "low", "high", "note"}} for every draw event.
    low / high bound the fragment count where the method is an estimate (None when unknown)."""
    matched = frustum_match(events, frustum["items"]) if frustum else {}
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
        elif ev["index"] in matched:
            its = matched[ev["index"]]
            raster = sum(i["raster_px"] for i in its)
            visible = sum(i["visible_px"] for i in its)
            n_mesh = len(ev["meshes"] or [ev["mesh"]])
            part = f"{len(its)} of {n_mesh} meshes in frustum.json" if len(its) < n_mesh else None
            clip = any(i["alpha_clip"] for i in its)
            if ev["stage"] == "opaque":
                px = max(diff or 0, raster)
                note = "; ".join(x for x in (part, "alpha test: undercounted without RenderDoc" if clip else None) if x)
                r.update(pixels=px, method="frustum", low=max(diff or 0, visible), high=px, note=note or None)
            elif diff is not None:
                r.update(high=max(diff, raster), note=part)
        if r["pixels"] is None:
            r.update(pixels=0, method="none", note="no pixel count")
        out[ev["index"]] = r
    return out
