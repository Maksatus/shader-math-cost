"""Fragment shader threads of a draw = 2x2 quads with a sample that passes the depth test x 4: Mali (any GPU) runs
whole quads, so a small triangle's empty lanes cost as much as its pixels. Counted by rasterizing the draw's post-VS
triangles (adapters/renderdoc/rd_draws.py) in frame order with per-sample depth per depth target, in batches of
triangles; validated against
Mali's own counters (quads past early ZS within 0.3%, Samples Passed within 0.01% on VATyakov K31_Heavy, MSAA and not).
Samples per draw are returned to check against the capture's Samples Passed (stencil is not modelled; a discarding
draw stores depth only where the capture's depth changed, its "written" mask from rd_draws).
Opaque draws (count(visible=...)): Mali's Forward Pixel Kill drops queued fragments a later opaque draw covers, so their
quads count only where the fragment is the one left at the end of the render pass (its depth = the pass's final depth);
Pixel 7 shader knockouts: vat crowd model / phone 2.26 -> 1.11."""
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
    out, src = [t[whole]], [np.nonzero(whole)[0]]
    crossing = np.nonzero(~whole & (d >= 0).any(axis=2).all(axis=0))[0]
    for i, tri in zip(crossing, t[crossing]):
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
            src.append(np.full(len(poly) - 2, i))
    # draw order kept: overlapping triangles of one draw pass the depth test in it
    return np.concatenate(out)[np.argsort(np.concatenate(src), kind="stable")]


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


BATCH = 1 << 22  # candidate pixels rasterized at once


def _top_left(x, y):
    """-> (sign of twice the area, [3 bool arrays]: a sample on that edge belongs to the triangle (top-left rule, y
    down, edges oriented so inside > 0)), in float32 as the vertices."""
    import numpy as np
    ax, ay, bx, by, cx, cy = x[:, 0], y[:, 0], x[:, 1], y[:, 1], x[:, 2], y[:, 2]
    sg = np.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    edges = []
    for dx, dy in ((bx - ax, by - ay), (cx - bx, cy - by), (ax - cx, ay - cy)):
        dx, dy = sg * dx, sg * dy
        edges.append(((dy == 0) & (dx > 0)) | (dy < 0))
    return sg, edges


def _batches(live, size, limit):
    """Triangles in draw order, cut where their summed bounding boxes pass limit."""
    start, total = 0, 0
    for i, n in enumerate(size.tolist()):
        if total and total + n > limit:
            yield live[start:i]
            start, total = i, 0
        total += n
    if start < len(live):
        yield live[start:]


def _ordered(f32):
    """float32 -> uint32 ordered as the floats."""
    import numpy as np
    b = f32.view(np.uint32)
    return np.where(b >> 31, ~b, b | np.uint32(0x80000000)).astype(np.uint32)


def _unordered(u):
    import numpy as np
    return np.where(u >> 31, u & np.uint32(0x7FFFFFFF), ~u).astype(np.uint32).view(np.float32)


def _raster(batch, x, y, depth, tl, box, w, h, ns, z, func, sign, steps, zclip, zwrite, written=None, final=None):
    """One batch of triangles in draw order -> (pixels, quads, samples); writes z. A sample's depth test sees the
    float32 depth every earlier triangle of the batch stored there (a running maximum: larger = closer). final: only
    samples whose float32 depth reaches this buffer count."""
    import numpy as np
    x0, x1, y0, y1 = (b[batch] for b in box)
    bw = x1 - x0
    n = bw * (y1 - y0)
    tri = np.repeat(np.arange(len(batch)), n)
    r = np.arange(len(tri)) - np.repeat(np.cumsum(n) - n, n)
    gy = np.repeat(y0, n) + r // np.repeat(bw, n)
    gx = np.repeat(x0, n) + r % np.repeat(bw, n)
    k = gy * w + gx
    t = batch[tri]
    ax, ay, bx, by, cx, cy = x[t, 0], y[t, 0], x[t, 1], y[t, 1], x[t, 2], y[t, 2]
    sg = tl[0][t].astype(np.float64)
    tls = [e[t] for e in tl[1]]
    shaded = np.zeros(len(t), dtype=bool)
    samples_n = 0
    for s, (sx, sy) in enumerate(PATTERN[ns]):
        px, py = gx + sx, gy + sy
        e0 = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        e1 = (cx - bx) * (py - by) - (cy - by) * (px - bx)
        e2 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx)
        inside = np.ones(e0.shape, dtype=bool)
        for e, te in zip((e0, e1, e2), tls):
            inside &= (sg * e > 0) | ((e == 0) & te)
        hit = np.nonzero(inside)[0]
        if not len(hit):
            continue
        e0, e1, e2, th = e0[hit], e1[hit], e2[hit], t[hit]
        area = e0 + e1 + e2
        zz = (e1 * depth[th, 0] + e2 * depth[th, 1] + e0 * depth[th, 2]) / np.where(area == 0, 1, area)
        if zclip:  # outside the near and far planes: clipped (NDC z in [0, 1])
            keep = (zz * sign >= 0) & (zz * sign <= 1)
            hit, zz = hit[keep], zz[keep]
        passed = np.ones(len(hit), dtype=bool)
        if steps and (z is not None or final is not None):
            zz = sign * np.round(np.clip(zz * sign, 0, 1) * steps) / steps
        if func == "Never":
            passed[:] = False
        elif z is not None and (func != "AlwaysTrue" or zwrite):
            kh = k[hit]
            cur = z[s, kh].astype(np.float64)
            writes = None if written is None else written[kh]  # discard: only where the capture's depth changed
            if zwrite and func not in ("Equal", "NotEqual", "AlwaysTrue"):
                order = np.lexsort((tri[hit], kh))
                ks = kh[order]
                group = np.unique(ks, return_inverse=True)[1].astype(np.uint64)
                zr = zz if writes is None else np.where(writes, zz, -np.inf)
                run = np.maximum.accumulate((group << np.uint64(32)) | _ordered(zr[order].astype(np.float32)))
                same = np.nonzero(ks[1:] == ks[:-1])[0] + 1
                before = cur[order]
                before[same] = np.maximum(before[same],
                                          _unordered((run[same - 1] & np.uint64(0xFFFFFFFF)).astype(np.uint32)))
                cur[order] = before
            if func in ("Greater", "Less"):
                passed = zz > cur
            elif func in ("GreaterEqual", "LessEqual"):
                passed = zz >= cur
            elif func == "Equal":
                passed = np.abs(zz - cur) <= 1e-6
            elif func == "NotEqual":
                passed = np.abs(zz - cur) > 1e-6
            if zwrite:
                stored = passed if writes is None else passed & writes
                kp, zp = kh[stored], zz[stored].astype(np.float32)
                if func in ("AlwaysTrue", "Equal", "NotEqual"):  # the last triangle's write stays
                    last = np.lexsort((tri[hit][stored], kp))
                    z[s, kp[last]] = zp[last]
                else:
                    np.maximum.at(z[s], kp, zp)
        if final is not None:
            passed &= zz.astype(np.float32) >= final[s, k[hit]]
        samples_n += int(passed.sum())
        shaded[hit[passed]] = True
    ks, ts = k[shaded], tri[shaded]
    quads = len(np.unique(ts * (w * h) + (ks // w // 2) * w + (ks % w) // 2)) if len(ks) else 0
    return len(ks), quads, samples_n


def triangles(meta, blob):
    """rd_draws' {eid: info with "parts"} and the bytes of its .bin -> (meta with "tris" / "offset", float32 [n, 3, 4]
    clip-space triangles of every draw in turn)."""
    import numpy as np
    out, parts, at = {}, [], 0
    for eid in sorted(meta, key=int):
        m = meta[eid]
        if "error" in m:
            out[eid] = m
            continue
        n = 0
        for pos_at, verts, idx_at, count, width in m["parts"]:
            pos = np.frombuffer(blob, np.float32, verts * 4, pos_at).reshape(-1, 4)
            idx = (np.frombuffer(blob, np.uint16 if width == 2 else np.uint32, count, idx_at).astype(np.int64)
                   if idx_at is not None else np.arange(count))
            if m["topology"] == "TriangleList":
                idx = idx[:len(idx) - len(idx) % 3].reshape(-1, 3)
            else:
                j = np.arange(max(0, len(idx) - 2))
                odd = j % 2 == 1
                idx = np.stack([np.where(odd, idx[j + 1], idx[j]), np.where(odd, idx[j], idx[j + 1]), idx[j + 2]], 1)
            parts.append(pos[idx])
            n += len(idx)
        out[eid] = {**{a: b for a, b in m.items() if a != "parts"}, "tris": n, "offset": at}
        if m.get("written"):
            off, size = m["written"]
            out[eid]["written"] = np.unpackbits(np.frombuffer(blob, np.uint8, size, off), bitorder="little").astype(bool)
        at += n
    return out, np.concatenate(parts) if parts else np.zeros((0, 3, 4), np.float32)


def count(meta, tris, actions, visible=()):
    """meta: triangles()' {eid: info}, tris: float32 [n, 3, 4], actions: the capture's [{"event", "name"}], visible:
    eids of opaque draws (see the module doc). -> {eid: {"pixels", "quads", "samples"}} in frame order, a visible
    draw's "quads" those left at the end of its pass ("drawn": past the depth test); errors pass through."""
    import numpy as np  # only this step needs it (pyproject: numpy)
    zbufs, out, done = {}, {}, set()
    later, finals, cur = {}, {}, None
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
        if b != cur:
            _keep_finals(later, finals, cur, zbufs)
            cur = b
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
        written = m.get("written")
        written = written[:w * h] if written is not None and 0 <= len(written) - w * h < 8 else None
        fmt = m.get("depth_format") or ""
        # unorm depth is stored and compared rounded: equal after rounding passes GreaterEqual
        steps = 65535.0 if fmt.startswith("D16") else 16777215.0 if fmt.startswith("D24") else None
        tl = _top_left(x, y)
        x0 = np.maximum(np.floor(x.min(axis=1)).astype(np.int64), rx0)
        x1 = np.minimum(np.ceil(x.max(axis=1)).astype(np.int64), rx1)
        y0 = np.maximum(np.floor(y.min(axis=1)).astype(np.int64), ry0)
        y1 = np.minimum(np.ceil(y.max(axis=1)).astype(np.int64), ry1)
        live = np.nonzero((x0 < x1) & (y0 < y1) & (tl[0] != 0))[0]
        size = (x1 - x0)[live] * (y1 - y0)[live]
        # Equal / NotEqual with depth writes depend on every earlier write: one triangle per batch
        one = func in ("Equal", "NotEqual") and m["zwrite"] and z is not None
        px_n = quads_n = samples_n = 0
        for batch in _batches(live, size, 1 if one else BATCH):
            p, q, n = _raster(batch, x, y, depth, tl, (x0, x1, y0, y1), w, h, ns, z, func, sign, steps, zclip,
                              m["zwrite"], written)
            px_n, quads_n, samples_n = px_n + p, quads_n + q, samples_n + n
        out[eid] = {"pixels": px_n, "quads": quads_n, "samples": samples_n}
        if eid in visible and z is not None and not one:
            later.setdefault(cur, []).append((eid, m["depth"], (x, y, depth, tl, (x0, x1, y0, y1), w, h, ns), live, size,
                                              (sign, steps, zclip)))
    _keep_finals(later, finals, cur, zbufs)
    for eid, final, (x, y, depth, tl, box, w, h, ns), live, size, (sign, steps, zclip) in finals.values():
        q = sum(_raster(batch, x, y, depth, tl, box, w, h, ns, None, "AlwaysTrue", sign, steps, zclip, False,
                        final=final)[1] for batch in _batches(live, size, BATCH))
        out[eid] = {**out[eid], "quads": q, "drawn": out[eid]["quads"]}
    return out


def _keep_finals(later, finals, b, zbufs):
    """At the end of render pass b: its visible draws get a copy of their depth target as it is now."""
    copies = {}
    for eid, d, *rest in later.pop(b, []):
        if d not in copies:
            copies[d] = zbufs[d].copy() if d in zbufs else None
        if copies[d] is not None:
            finals[eid] = (eid, copies[d], *rest)
