# Runs inside qrenderdoc --python: imported by rd_device.py (the device's replay), or as a script on a desktop capture
# (In: PARETOGPU_RDC, PARETOGPU_OUT). Needs PARETOGPU_LIB and PARETOGPU_ROOT (core/loops numbers and bounds the loops as
# the pricing does). measure() walks the draws once in frame order and writes:
# <out> {"geometry": {eid: raster state (core/quads) + "parts": [[pos offset, vertices, idx offset or None, indices,
# index bytes]] per instance} or {"error"}, "loops": {eid: {"loops", "nested", "pixels", "sums": [summed n per loop]}
# or {"error"}}, "_seconds"}; <out>.bin: float32 x4 post-VS positions and the post-VS index buffers.
# Geometry: every draw with a fragment shader or a depth write; a discarding draw that writes depth also gets
# "written": [offset, bytes] of a bit per depth texel in <out>.bin, 1 where it changed the depth. Loops: draws whose
# fragment shader has an outer loop the call's uniform bytes do not bound; its shader is swapped for rd_loop_counter's
# (three loops per swap, a round each). A call that
# writes depth: its pixels are those whose colour or depth it changed, read before and after it. One that does not:
# its target is cleared before it (RenderDoc's ClearBeforeDraw overlay, which makes the next move ~0.3 s dearer) and
# summed on the replaying GPU by GetHistogram.
import json
import os
import struct
import sys
import time
import traceback
from collections import Counter

import renderdoc as rd

TOPOLOGY = {3: "TriangleList", 4: "TriangleStrip"}
EXACT_SRC = {"One", "SrcAlpha"}  # these leave a count written with alpha 1 as is
TIMING = Counter()
NONZERO = bytes([0] + [1] * 255)
WRITES = (rd.ActionFlags.Drawcall | rd.ActionFlags.Dispatch | rd.ActionFlags.Clear | rd.ActionFlags.Copy
          | rd.ActionFlags.Resolve)


def _small_float(bits, mbits):
    """Unsigned small float of R11G11B10 (5-bit exponent)."""
    e, m = bits >> mbits, bits & ((1 << mbits) - 1)
    return (1 + m / (1 << mbits)) * 2.0 ** (e - 15) if e else m / (1 << mbits) * 2.0 ** -14


def decoder(fmt):
    """-> (scale to write, decode(pixel bytes) -> (r, g, b), [largest n + 1 held exactly per channel]) or an error."""
    if fmt.type == rd.ResourceFormatType.R11G11B10:
        def dec(px):
            v = struct.unpack("<I", px)[0]
            return _small_float(v & 0x7FF, 6), _small_float((v >> 11) & 0x7FF, 6), _small_float(v >> 22, 5)
        return 1.0, dec, [128, 128, 64]
    if fmt.type != rd.ResourceFormatType.Regular or fmt.SRGBCorrected() or fmt.compCount < 3:
        return f"target format {fmt.Name()} cannot hold a count"
    n, w = fmt.compCount, fmt.compByteWidth
    order = (2, 1, 0) if fmt.BGRAOrder() else (0, 1, 2)
    if fmt.compType == rd.CompType.Float and w in (2, 4):
        code = f"<{n}{'e' if w == 2 else 'f'}"
        return 1.0, lambda px: tuple(struct.unpack(code, px)[c] for c in order), [2048 if w == 2 else 1 << 24] * 3
    if fmt.compType == rd.CompType.UNorm and w == 1:
        return 1 / 255, lambda px: tuple(px[c] / 255 for c in order), [255] * 3
    return f"target format {fmt.Name()} cannot hold a count"


def loop_plan(st, outer, textures, loops_model):
    """outer: (continue labels, nested, bounds) of its fragment shader. -> {"conts", "nested", "rt", "depth", "scale",
    "decode", "exact"}, None (no loop to count) or an error."""
    conts, nested, bounds = outer
    if not conts:
        return None
    if bounds is not None and len(bounds) == len(conts) and \
            all(loops_model.bound_value(b, st["blocks"]) is not None for b in bounds):
        return None
    if st["blend"] != "One Zero" and st["blend"].split()[0] not in EXACT_SRC:
        return "its blend mode changes the count"
    if not st["outs"]:
        return "no colour target"
    dec = decoder(textures[st["outs"][0]].format)
    if isinstance(dec, str):
        return dec
    if st["write_mask"] & 7 != 7:
        return "its colour write mask hides a channel"
    return {"conts": conts, "nested": nested, "rt": st["outs"][0], "depth": st["depth"], "scale": dec[0],
            "decode": dec[1], "exact": dec[2], "clear": not (st["zwrite"] and st["depth"])}


def raster(st, textures):
    r = dict(st["raster"])
    if r["topology"] not in TOPOLOGY:
        return {"error": f"topology {r['topology']}"}
    r["topology"] = TOPOLOGY[r["topology"]]
    target = textures.get((st["outs"] or [st["depth"]])[0])
    depth = textures.get(st["depth"])
    r["size"] = [target.width, target.height] if target else None
    r["depth_format"] = depth.format.Name() if depth else None
    return r


def depth_bytes(c, tid):
    t = time.time()
    data = bytes(c.GetTextureData(tid, rd.Subresource(0, 0, 0)))
    TIMING["textures"] += time.time() - t
    return data


def written(before, after, tex):
    """-> a bit per texel (little-endian bit order), 1 where the call changed the stored depth (sample 0); None if the
    layout is unknown."""
    n = tex.width * tex.height
    if not n or len(after) != len(before) or len(after) % n:
        return None
    size, name = len(after) // n, tex.format.Name()
    depth = 4 if size >= 4 and name.startswith("D32") else size if size in (2, 4) else None
    if depth is None:
        return None
    if before == after:
        return bytes((n + 7) // 8)
    t = time.time()
    flags = (int.from_bytes(before, "little") ^ int.from_bytes(after, "little")).to_bytes(len(after), "little")
    flags = flags.translate(NONZERO)
    acc = 0
    for i in range(depth):
        acc |= int.from_bytes(flags[i::size], "little")
    per = acc.to_bytes(n, "little") + bytes(-n % 8)
    bits = 0
    for j in range(8):
        bits |= int.from_bytes(per[j::8], "little") << j
    TIMING["mask"] += time.time() - t
    return bits.to_bytes(len(per) // 8, "little")


def geometry(c, instances, blob, offset):
    """-> (parts, new offset): every instance's post-VS positions (x, y, z, w) and indices appended to blob."""
    parts = []
    for inst in range(max(1, instances)):
        t = time.time()
        m = c.GetPostVSData(inst, 0, rd.MeshDataStage.VSOut)
        TIMING["postvs"] += time.time() - t
        if m.vertexResourceId == rd.ResourceId.Null() or not m.numIndices:
            continue
        t = time.time()
        data = bytes(c.GetBufferData(m.vertexResourceId, m.vertexByteOffset, 0))
        pos = b"".join(data[i:i + 16] for i in range(0, len(data) - 15, m.vertexByteStride))
        idx = None
        if m.indexResourceId != rd.ResourceId.Null() and m.indexByteStride in (2, 4):
            idx = bytes(c.GetBufferData(m.indexResourceId, m.indexByteOffset, m.numIndices * m.indexByteStride))
        TIMING["read"] += time.time() - t
        blob.write(pos)
        part = [offset, len(pos) // 16, None, m.numIndices, m.indexByteStride if idx else 0]
        offset += len(pos)
        if idx:
            blob.write(idx)
            part[2] = offset
            offset += len(idx)
        parts.append(part)
    return parts, offset


def read_targets(c, plan, textures):
    """-> (colour, depth) bytes of a counted call's targets at the replay's event (sample 0)."""
    t = time.time()
    sub = rd.Subresource(0, 0, 0)
    color = bytes(c.GetTextureData(textures[plan["rt"]].resourceId, sub))
    depth = bytes(c.GetTextureData(textures[plan["depth"]].resourceId, sub)) if plan["depth"] else b""
    TIMING["textures"] += time.time() - t
    return color, depth


def histogram_sum(c, tid, channel, top, scale):
    """-> (texels holding a count >= 1, their sum) in one channel; buckets of 256 exact integers each."""
    n = s = 0
    for lo in range(0, top + 1, 256):
        h = c.GetHistogram(tid, rd.Subresource(0, 0, 0), rd.CompType.Typeless, (lo - 0.5) * scale,
                           (lo + 255.5) * scale, tuple(i == channel for i in range(4)))
        for i, k in enumerate(h):
            if lo + i >= 1:
                n += k
                s += (lo + i) * k
    return n, s


def cleared_sums(rp, plan, n, textures):
    """At the call, its target cleared before it: -> (pixels it wrote, [summed counts of its n loops]) or an error."""
    t = time.time()
    rt = textures[plan["rt"]].resourceId
    td = rd.TextureDisplay()
    td.resourceId = rt
    td.overlay = rd.DebugOverlay.ClearBeforeDraw
    rp.output().SetTextureDisplay(td)
    rp.output().Display()
    _, mx = rp.c.GetMinMax(rt, rd.Subresource(0, 0, 0), rd.CompType.Typeless)
    pixels, sums = None, []
    for k in range(n):
        top = int(round(mx.floatValue[k] / plan["scale"]))
        if top > plan["exact"][k]:
            return f"trip counts up to {top - 1} exceed what the target format holds exactly"
        px, s = histogram_sum(rp.c, rt, k, top, plan["scale"])
        if pixels is not None and px != pixels:
            return "the channels of the count disagree on the pixels written"
        pixels = px
        sums.append(s - px)
    TIMING["cleared"] += time.time() - t
    return pixels or 0, sums


def changed_sums(before, after, plan, n, keepalive):
    """-> (pixels the call wrote: colour or depth changed, [summed counts of its n loops]) or an error."""
    t = time.time()
    (c0, d0), (c1, d1) = before, after
    if len(c0) != len(c1) or len(d0) != len(d1):
        return "the target changed size"
    texels = plan["texels"]
    cs, ds = len(c1) // texels, len(d1) // texels
    values = Counter()

    def same(a, b):
        return c0[a * cs:b * cs] == c1[a * cs:b * cs] and d0[a * ds:b * ds] == d1[a * ds:b * ds]
    for b0 in range(0, texels, 4096):
        if same(b0, min(b0 + 4096, texels)):
            continue
        keepalive()
        for s0 in range(b0, min(b0 + 4096, texels), 64):
            s1 = min(s0 + 64, texels)
            if same(s0, s1):
                continue
            for i in range(s0, s1):
                pc = c1[i * cs:(i + 1) * cs]
                if pc != c0[i * cs:(i + 1) * cs] or d1[i * ds:(i + 1) * ds] != d0[i * ds:(i + 1) * ds]:
                    values[pc] += 1
    sums = [0] * n
    for pc, count in values.items():
        vals = plan["decode"](pc)
        for k in range(n):
            v = round(vals[k] / plan["scale"])
            if v > plan["exact"][k]:
                return f"trip counts up to {v - 1} exceed what the target format holds exactly"
            sums[k] += (v - 1) * count
    TIMING["compare"] += time.time() - t
    return sum(values.values()), sums


def measure(rp, f, out_path, step):
    """rp: rd_replay.Replay, f: rd_replay.state of its capture. Writes <out_path> and <out_path>.bin."""
    sys.path.insert(0, os.environ["PARETOGPU_ROOT"])
    import rd_loop_counter as counter
    import rd_spirv
    from paretogpu.core import loops as loops_model
    textures = {int(x.resourceId): x for x in rp.c.GetTextures()}
    instances, writes = {}, []

    def walk(items):
        for a in items:
            instances[a.eventId] = a.numInstances
            if not a.children and a.flags & WRITES:
                writes.append(a.eventId)
            walk(a.children)
    walk(rp.c.GetRootActions())
    writes.sort()
    nxt = dict(zip(writes, writes[1:]))
    prev = {b: a for a, b in nxt.items()}
    shaded = sorted(e for e, st in f.calls.items() if "frag" in st.get("stages", {}))
    draws = sorted(set(shaded) | {e for e, st in f.calls.items() if "raster" in st and st["zwrite"] and st["depth"]})
    discards = {}
    for e in shaded:
        st = f.calls[e]
        if st["zwrite"] and st["depth"]:
            module = st["stages"]["frag"][0]
            if module not in discards:
                discards[module] = rd_spirv.discards(f.modules[module])
    masked = {e for e in shaded if f.calls[e]["zwrite"] and f.calls[e]["depth"]
              and discards[f.calls[e]["stages"]["frag"][0]]}
    after_of = {prev[e]: e for e in masked if prev.get(e) in draws}
    pre = {}
    geo, loops, plans, outer = {}, {}, {}, {}
    for e in shaded:
        module, entry = f.calls[e]["stages"]["frag"]
        if module not in outer:  # once per shader: a replay server drops a client silent for 15 s
            outer[module] = (*counter.outer_loops(f.modules[module]), loops_model.spirv_bounds(f.modules[module]))
        p = loop_plan(f.calls[e], outer[module], textures, loops_model)
        if isinstance(p, str):
            loops[str(e)] = {"error": p}
        elif p:
            p.update(module=module, entry=entry, texels=textures[p["rt"]].width * textures[p["rt"]].height,
                     pixels=None, sums=[0] * len(p["conts"]))
            plans[e] = p
    ids = {int(x.resourceId): x.resourceId for x in rp.c.GetResources()}
    rounds = max(((len(p["conts"]) + 2) // 3 for p in plans.values()), default=0)
    offset, full = 0, True
    with open(out_path + ".bin", "wb") as blob:
        for r in range(max(1, rounds)):
            swaps, active = {}, {}
            for e, p in plans.items():
                part = p["conts"][3 * r:3 * r + 3]
                if part and str(e) not in loops:
                    active[e] = len(part)
                    if ids[p["module"]] not in swaps:
                        swaps[ids[p["module"]]] = (p["entry"], counter.counting(f.modules[p["module"]], part,
                                                                                p["scale"]))
            for module, err in rp.swap(swaps).items():
                for e in [e for e in active if ids[plans[e]["module"]] == module]:
                    loops[str(e)] = {"error": err}
                    del active[e]
            events = sorted(set(active) | (set(draws) if r == 0 else set()))
            last = None  # (event, targets, (colour, depth) after it)
            for n, e in enumerate(events):
                if n % 20 == 0:
                    step(f"draws: round {r + 1}/{max(1, rounds)}, call {n + 1}/{len(events)}")
                p = plans[e] if e in active else None
                before = None
                if p and not p["clear"]:
                    if last and last[0] == prev.get(e) and last[1] == (p["rt"], p["depth"]):
                        before = last[2]
                    else:
                        t = time.time()
                        rp.go(e - 1, full)
                        full = False
                        TIMING["replay"] += time.time() - t
                        before = read_targets(rp.c, p, textures)
                if r == 0 and e in masked and e not in pre and before is not None and p["depth"] == f.calls[e]["depth"]:
                    pre[e] = before[1]
                if r == 0 and e in masked and e not in pre:
                    t = time.time()
                    rp.go(e - 1, full)
                    full = False
                    TIMING["replay"] += time.time() - t
                    pre[e] = depth_bytes(rp.c, textures[f.calls[e]["depth"]].resourceId)
                t = time.time()
                rp.go(e, full)
                full = False
                TIMING["replay"] += time.time() - t
                if r == 0:
                    m, after = raster(f.calls[e], textures), None
                    if "error" not in m:
                        m["parts"], offset = geometry(rp.c, instances.get(e, 1), blob, offset)
                        if e in masked:
                            tex = textures[f.calls[e]["depth"]]
                            after = depth_bytes(rp.c, tex.resourceId)
                            mask = written(pre.pop(e), after, tex)
                            if mask is not None:
                                blob.write(mask)
                                m["written"] = [offset, len(mask)]
                                offset += len(mask)
                    geo[str(e)] = m
                    if e in after_of:
                        q = after_of[e]
                        same = after is not None and f.calls[q]["depth"] == f.calls[e]["depth"]
                        pre[q] = after if same else depth_bytes(rp.c, textures[f.calls[q]["depth"]].resourceId)
                last = None
                if p:
                    if p["clear"]:
                        got = cleared_sums(rp, p, active[e], textures)
                    else:
                        after = read_targets(rp.c, p, textures)
                        got = changed_sums(before, after, p, active[e], rp.keepalive)
                        last = (e, (p["rt"], p["depth"]), after)
                    if isinstance(got, str):
                        loops[str(e)] = {"error": got}
                    else:
                        p["pixels"] = got[0] if p["pixels"] is None else p["pixels"]
                        p["sums"][3 * r:3 * r + active[e]] = got[1]
                elif nxt.get(e) in active and not plans[nxt[e]]["clear"]:
                    q = plans[nxt[e]]
                    if (f.calls[e].get("outs") or [None])[0] == q["rt"]:
                        last = (e, (q["rt"], q["depth"]), read_targets(rp.c, q, textures))
            rp.unswap()
            if not rp.alive():
                raise RuntimeError("RenderDoc's server on the device is gone: the walk's answers are empty")
    for e, p in plans.items():
        if str(e) not in loops:
            loops[str(e)] = {"loops": len(p["conts"]), "nested": p["nested"], "pixels": p["pixels"] or 0,
                             "sums": p["sums"]}
    with open(out_path + ".tmp", "w") as fh:
        json.dump({"geometry": geo, "loops": loops, "_seconds": {k: round(v, 1) for k, v in TIMING.items()}}, fh)
    os.replace(out_path + ".tmp", out_path)


def main():
    sys.path.insert(0, os.environ["PARETOGPU_LIB"])
    import rd_replay
    out_path = os.environ["PARETOGPU_OUT"]
    rdc, serial = rd_replay.environment()

    def step(text):
        with open(out_path + ".progress", "w") as fh:
            fh.write(text)
    rp = rd_replay.Replay(rdc, serial, step=step)
    measure(rp, rd_replay.state(rp.c, rdc), out_path, step)
    rp.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.environ["PARETOGPU_OUT"] + ".error", "w") as fh:
            fh.write(traceback.format_exc())
    os._exit(0)
