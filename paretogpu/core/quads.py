"""Fragment shader threads of a draw = 2x2 quads with a sample that passes the depth test x 4: Mali (any GPU) runs
whole quads, so a small triangle's empty lanes cost as much as its pixels. Counted by rasterizing the draw's post-VS
triangles (adapters/renderdoc/rd_geometry.py) in frame order with per-sample depth per depth target; validated against
Mali's own counters (quads past early ZS within 0.3%, Samples Passed within 0.01% on VATyakov K31_Heavy, MSAA and not).
Samples per draw are returned to check against the capture's Samples Passed (stencil and discard are not modelled)."""
import bisect
import re

PATTERN = {1: [(0.5, 0.5)], 2: [(0.75, 0.75), (0.25, 0.25)],
           4: [(0.375, 0.125), (0.875, 0.375), (0.125, 0.625), (0.625, 0.875)]}
BEGIN = "vkCmdBeginRenderPass"
DEPTH_CLEAR = re.compile(r"\bDS?=Clear\b|ClearDepthStencilImage")
CLOSER = {"Greater": 1, "GreaterEqual": 1, "Less": -1, "LessEqual": -1}  # sign: larger = closer after it


def passes(actions):
    """-> (sorted render pass begin events, sorted events that clear the depth of their pass: load op or clear)."""
    begins = sorted(a["event"] for a in actions if (a.get("name") or "").startswith(BEGIN))
    return begins, sorted(a["event"] for a in actions if DEPTH_CLEAR.search(a.get("name") or ""))


def _clipped(t):
    """Triangles clipped to the depth planes of the Vulkan clip volume (0 <= z <= w; reversed Z puts the near one at
    z = w) the way the GPU does: a crossing triangle becomes the triangles of its inside part, outside ones drop."""
    import numpy as np
    planes = (lambda v: v[..., 2], lambda v: v[..., 3] - v[..., 2])
    d = np.stack([f(t) for f in planes])  # [2, n, 3]
    whole = (d >= 0).all(axis=(0, 2))
    out = [t[whole]]
    for tri in t[~whole & (d >= 0).any(axis=2).all(axis=0)]:
        poly = list(tri)
        for f in planes:
            nxt = []
            for j in range(len(poly)):
                a, b = poly[j], poly[(j + 1) % len(poly)]
                da, db = f(a), f(b)
                if da >= 0:
                    nxt.append(a)
                if (da >= 0) != (db >= 0):
                    nxt.append(a + da / (da - db) * (b - a))
            poly = nxt
            if len(poly) < 3:
                break
        if len(poly) >= 3:
            out.append(np.array([[poly[0], poly[k], poly[k + 1]] for k in range(1, len(poly) - 1)], dtype=np.float32))
    return np.concatenate(out)


def _bias(x, y, z, m):
    """Vulkan depth bias per triangle: slope x max |dz/dx|, |dz/dy| + constant x the depth format's resolution."""
    import numpy as np
    constant, clamp, slope = m["bias"]
    fmt = m.get("depth_format") or ""
    r = 2.0 ** -16 if fmt.startswith("D16") else 2.0 ** -24 if fmt.startswith("D24") else 2.0 ** -23
    ex, ey = x[:, 1:] - x[:, :1], y[:, 1:] - y[:, :1]
    ez = z[:, 1:] - z[:, :1]
    det = ex[:, 0] * ey[:, 1] - ex[:, 1] * ey[:, 0]
    det = np.where(det == 0, 1, det)
    dzdx = (ez[:, 0] * ey[:, 1] - ez[:, 1] * ey[:, 0]) / det
    dzdy = (ex[:, 0] * ez[:, 1] - ex[:, 1] * ez[:, 0]) / det
    b = slope * np.maximum(np.abs(dzdx), np.abs(dzdy)) + constant * r
    if clamp > 0:
        b = np.minimum(b, clamp)
    elif clamp < 0:
        b = np.maximum(b, clamp)
    return b


def _screen(t, vp):
    """clip -> (x, y, z) arrays [n, 3] in target pixels."""
    ndc = t[:, :, :3] / t[:, :, 3:4]
    return (vp[0] + (ndc[:, :, 0] + 1) * 0.5 * vp[2], vp[1] + (ndc[:, :, 1] + 1) * 0.5 * vp[3], ndc[:, :, 2])


def _kept(x, y, m):
    import numpy as np
    area = -0.5 * ((x[:, 0] * y[:, 1] - x[:, 1] * y[:, 0]) + (x[:, 1] * y[:, 2] - x[:, 2] * y[:, 1])
                   + (x[:, 2] * y[:, 0] - x[:, 0] * y[:, 2]))
    if m["viewport"][3] < 0:  # a flipped viewport mirrors the winding
        area = -area
    front = area > 0 if m["front_ccw"] else area < 0
    return {"Back": front, "Front": ~front}.get(m["cull"], np.ones_like(front))


def count(meta, tris, actions):
    """meta: rd_geometry's {eid: info}, tris: float32 [n, 3, 4], actions: the capture's [{"event", "name"}].
    -> {eid: {"pixels", "quads", "samples"}} in frame order; errors pass through."""
    import numpy as np  # only this step needs it (pyproject: numpy)
    zbufs, out, done = {}, {}, set()
    begins, clears = passes(actions)
    for eid in sorted(meta, key=int):
        m = meta[eid]
        if "error" in m:
            out[eid] = m
            continue
        w, h = m["size"]
        ns = m["samples"] if m["samples"] in PATTERN else 1
        # a clear of this draw's pass (its load op, or a clear inside it before the draw) resets its depth target
        b = begins[bisect.bisect_right(begins, int(eid)) - 1] if begins and begins[0] <= int(eid) else 0
        for c in clears[bisect.bisect_left(clears, b):bisect.bisect_left(clears, int(eid))]:
            if c not in done:
                done.add(c)
                zbufs.pop(m["depth"], None)
        z = zbufs.get(m["depth"]) if m["depth"] else None
        if m["depth"] and (z is None or z.shape != (ns, w * h)):
            z = zbufs[m["depth"]] = np.full((ns, w * h), -np.inf, dtype=np.float32)
        func = m["zfunc"] if m["ztest"] and z is not None else "AlwaysTrue"
        sign = CLOSER.get(func, 1)
        t = tris[m["offset"]:m["offset"] + m["tris"]]
        if m.get("depth_clip", True):
            t = _clipped(t)
        t = t[(t[:, :, 3] > 1e-9).all(axis=1)]
        x, y, depth = _screen(t, m["viewport"])
        keep = _kept(x, y, m)
        x, y, depth = x[keep], y[keep], depth[keep]
        if m.get("bias"):
            depth = depth + _bias(x, y, depth, m)[:, None]
        depth = depth * sign
        # rasterized only inside the viewport (the clip volume), the scissor and the target
        vx, vy, vw, vh = m["viewport"]
        rx0, rx1 = max(min(vx, vx + vw), 0), min(max(vx, vx + vw), w)
        ry0, ry1 = max(min(vy, vy + vh), 0), min(max(vy, vy + vh), h)
        if m.get("scissor"):
            sx_, sy_, sw_, sh_ = m["scissor"]
            rx0, rx1, ry0, ry1 = max(rx0, sx_), min(rx1, sx_ + sw_), max(ry0, sy_), min(ry1, sy_ + sh_)
        rx0, rx1, ry0, ry1 = int(np.floor(rx0)), int(np.ceil(rx1)), int(np.floor(ry0)), int(np.ceil(ry1))
        zclip = m.get("depth_clip", True)
        fmt = m.get("depth_format") or ""
        # unorm depth is stored and compared rounded: equal after rounding passes GreaterEqual
        steps = 65535.0 if fmt.startswith("D16") else 16777215.0 if fmt.startswith("D24") else None
        px_n = quads_n = samples_n = 0
        for i in range(len(x)):
            x0, x1 = max(int(np.floor(x[i].min())), rx0), min(int(np.ceil(x[i].max())), rx1)
            y0, y1 = max(int(np.floor(y[i].min())), ry0), min(int(np.ceil(y[i].max())), ry1)
            if x0 >= x1 or y0 >= y1:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1), np.arange(y0, y1))
            gx, gy = gx.ravel(), gy.ravel()
            k = gy * w + gx
            shaded = np.zeros(k.shape, dtype=bool)
            ax, ay, bx, by, cx, cy = x[i, 0], y[i, 0], x[i, 1], y[i, 1], x[i, 2], y[i, 2]
            twice = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if twice == 0:
                continue
            sg = 1.0 if twice > 0 else -1.0
            # top-left rule: a sample on an edge belongs to one triangle (y down, edges oriented so inside > 0)
            tl = [(dy == 0 and dx > 0) or dy < 0 for dx, dy in
                  ((sg * (bx - ax), sg * (by - ay)), (sg * (cx - bx), sg * (cy - by)), (sg * (ax - cx), sg * (ay - cy)))]
            for s, (sx, sy) in enumerate(PATTERN[ns]):
                px, py = gx + sx, gy + sy
                e0 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
                e1 = (cx - bx) * (py - by) - (cy - by) * (px - bx)
                e2 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx)
                inside = np.ones(e0.shape, dtype=bool)
                for e, t in zip((e0, e1, e2), tl):
                    inside &= (sg * e > 0) | ((e == 0) & t)
                if not inside.any():
                    continue
                area = e0 + e1 + e2
                if zclip:  # outside the near and far planes: clipped (NDC z in [0, 1])
                    zn = ((e1 * depth[i, 0] + e2 * depth[i, 1] + e0 * depth[i, 2])
                          / np.where(area == 0, 1, area)) * sign
                    inside &= (zn >= 0) & (zn <= 1)
                hit = np.nonzero(inside)[0]
                passed = np.ones(len(hit), dtype=bool)
                if func == "Never":
                    passed[:] = False
                elif z is not None and (func != "AlwaysTrue" or m["zwrite"]):
                    zz = ((e1 * depth[i, 0] + e2 * depth[i, 1] + e0 * depth[i, 2])
                          / np.where(area == 0, 1, area))[hit]
                    if steps:
                        zz = sign * np.round(np.clip(zz * sign, 0, 1) * steps) / steps
                    cur = z[s, k[hit]]
                    if func in ("Greater", "Less"):
                        passed = zz > cur
                    elif func in ("GreaterEqual", "LessEqual"):
                        passed = zz >= cur
                    elif func == "Equal":
                        passed = np.abs(zz - cur) <= 1e-6
                    elif func == "NotEqual":
                        passed = np.abs(zz - cur) > 1e-6
                    if m["zwrite"]:
                        z[s, k[hit][passed]] = zz[passed]
                samples_n += int(passed.sum())
                shaded[hit[passed]] = True
            if shaded.any():
                ks = k[shaded]
                px_n += len(ks)
                quads_n += len(np.unique((ks // w // 2) * w + (ks % w) // 2))
        out[eid] = {"pixels": px_n, "quads": quads_n, "samples": samples_n}
    return out
