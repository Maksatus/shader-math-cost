# Runs inside qrenderdoc --python (RenderDoc's UI python, not the normal interpreter). In: Vulkan capture PARETOGPU_RDC
# and PARETOGPU_DEVICE (rd_replay.py), PARETOGPU_IN [{"key", "calls": [eid]}] (the calls of one draw event),
# PARETOGPU_ROOT (the repository: core/loops numbers the loops as the pricing does). Out: PARETOGPU_OUT {key: {"loops": n
# outer loops, "nested": [bool], "pixels", "lost", "means": [trip count per pixel]} or {"error"}}; <out>.progress,
# traceback -> <out>.error.
# A draw's fragment shader is swapped for rd_loop_counter's (trip counts as its colour), two loops per swap; one swap
# serves every draw of that shader, walked forward in frame order. Its pixels: the target before its first call vs
# after its last, both with the swap. A pixel an earlier draw of the same shader left with the same counts looks
# untouched: each pass averages over the pixels it saw; "pixels" is the most a pass saw, "lost" the share another did
# not.
import bisect
import json
import os
import struct
import sys
import time
import traceback
from collections import Counter

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]
counter = rd_replay = None  # imported in main(), an import error lands in <out>.error
TIMING = Counter()  # seconds per step, in the answer as "_seconds"
ALPHA_BLENDS = {"Zero", "One", "SrcAlpha", "InvSrcAlpha"}  # with alpha 1 these leave the written value exact


def _float(bits, mbits):
    """Unsigned small float of R11G11B10 (5-bit exponent)."""
    e, m = bits >> mbits, bits & ((1 << mbits) - 1)
    return (1 + m / (1 << mbits)) * 2.0 ** (e - 15) if e else m / (1 << mbits) * 2.0 ** -14


def _decoder(fmt):
    """-> (bytes per pixel, scale to write, decode(pixel bytes) -> (r, g, b)) or None."""
    if fmt.type == rd.ResourceFormatType.R11G11B10:
        def dec(px):
            v = struct.unpack("<I", px)[0]
            return _float(v & 0x7FF, 6), _float((v >> 11) & 0x7FF, 6), _float(v >> 22, 5)
        return 4, 1.0, dec
    if fmt.type != rd.ResourceFormatType.Regular or fmt.SRGBCorrected() or fmt.compCount < 3:
        return None
    n, w = fmt.compCount, fmt.compByteWidth
    order = (2, 1, 0) if fmt.BGRAOrder() else (0, 1, 2)
    if fmt.compType == rd.CompType.Float and w in (2, 4):
        code = f"<{n}{'e' if w == 2 else 'f'}"
        return n * w, 1.0, lambda px: tuple(struct.unpack(code, px)[c] for c in order)
    if fmt.compType == rd.CompType.UNorm and w == 1:
        return n, 1 / 255, lambda px: tuple(px[c] / 255 for c in order)
    return None


def _changed(before, after, size):
    """-> Counter {pixel bytes: pixels} of the pixels that differ; a draw changes few blocks of the target."""
    out, block = Counter(), size * 1024
    for o in range(0, len(after), block):
        if before[o:o + block] == after[o:o + block]:
            continue
        for i in range(o, min(o + block, len(after)), size):
            px = after[i:i + size]
            if px != before[i:i + size]:
                out[px] += 1
    return out


def survey(controller, calls):
    """At the draw's first call: -> {"shader", "raw", "entry", "conts", "nested", "rt", "size", "scale", "decode"} or
    an error string."""
    pipe = controller.GetPipelineState()
    refl = pipe.GetShaderReflection(rd.ShaderStage.Pixel)
    if refl is None:
        return "no fragment shader"
    raw = bytes(refl.rawBytes)
    conts, nested = counter.outer_loops(raw)
    info = {"shader": pipe.GetShader(rd.ShaderStage.Pixel), "raw": raw, "entry": refl.entryPoint, "conts": conts,
            "nested": nested, "calls": calls}
    if not conts:
        return info
    blends = pipe.GetColorBlends()
    b = blends[0] if blends else None
    if b and b.enabled and not {str(b.colorBlend.source).split(".")[-1],
                                str(b.colorBlend.destination).split(".")[-1]} <= ALPHA_BLENDS:
        return "its blend mode adds to the target"
    if b and (b.writeMask & 7) != 7:
        return "its colour write mask hides a channel"
    targets = [t for t in pipe.GetOutputTargets() if t.resource != rd.ResourceId.Null()]
    if not targets:
        return "no colour target"
    tex = next(t for t in controller.GetTextures() if t.resourceId == targets[0].resource)
    dec = _decoder(tex.format)
    if dec is None:
        return f"target format {tex.format.Name()} cannot hold a count"
    info.update(rt=targets[0].resource, size=dec[0], scale=dec[1], decode=dec[2])
    return info


def tally(before, after, info, n_loops):
    """-> (pixels, [summed trip counts]) of one pass."""
    got, n_px = [0.0] * n_loops, 0
    for px, n in _changed(before, after, info["size"]).items():
        r, g, m = info["decode"](px)
        if abs(m - counter.MARKER) > 0.01:
            continue
        n_px += n
        for k, v in enumerate((r, g)[:n_loops]):
            got[k] += (round(v / info["scale"]) - 1) * n
    return n_px, got


WRITES = (rd.ActionFlags.Drawcall | rd.ActionFlags.Dispatch | rd.ActionFlags.Clear | rd.ActionFlags.Copy
          | rd.ActionFlags.Resolve)


def writes(controller):
    """-> sorted event ids of the actions that can write a target (the previous read stays valid between them)."""
    out = []

    def walk(items):
        for a in items:
            if a.children:
                walk(a.children)
            elif a.flags & WRITES:
                out.append(a.eventId)
    walk(controller.GetRootActions())
    return sorted(out)


def count_group(rp, infos, done, progress, written):
    """infos: draws of one fragment shader in frame order -> fills done[key]; progress(draws of the group passed)."""
    first, n = infos[0], len(infos[0]["conts"])
    seen = {id(i): [] for i in infos}
    means = {id(i): [0.0] * n for i in infos}
    sub = rd.Subresource(0, 0, 0)
    for c in range(0, n, 2):
        part = len(first["conts"][c:c + 2])
        try:
            rp.set_swap(first["shader"], first["entry"], counter.counting(first["raw"], first["conts"][c:c + 2],
                                                                           first["scale"]))
        except ValueError as e:
            for i in infos:
                done[i["key"]] = {"error": str(e)}
            return
        try:
            full, last, after = True, None, None
            for i in infos:
                # the last read is still the target before this draw when nothing wrote in between
                if not (last and i["rt"] == last["rt"] and
                        bisect.bisect_left(written, i["calls"][0]) == bisect.bisect_right(written, last["calls"][-1])):
                    t = time.time()
                    rp.go(i["calls"][0] - 1, full)
                    full = False
                    TIMING["replay"] += time.time() - t
                    t = time.time()
                    after = bytes(rp.c.GetTextureData(i["rt"], sub))
                    TIMING["read"] += time.time() - t
                before = after
                t = time.time()
                rp.go(i["calls"][-1], full)
                full = False
                TIMING["replay"] += time.time() - t
                t = time.time()
                after = bytes(rp.c.GetTextureData(i["rt"], sub))
                TIMING["read"] += time.time() - t
                last = i
                if len(after) != len(before):
                    done[i["key"]] = {"error": "the target changed size"}
                    continue
                t = time.time()
                px, got = tally(before, after, i, part)
                TIMING["compare"] += time.time() - t
                seen[id(i)].append(px)
                means[id(i)][c:c + part] = [g / px if px else 0.0 for g in got]
                progress(c // 2 * len(infos) + infos.index(i) + 1, (n + 1) // 2 * len(infos))
        finally:
            rp.unswap()
    for i in infos:
        if i["key"] in done:
            continue
        top = max(seen[id(i)])
        done[i["key"]] = {"loops": n, "nested": i["nested"], "pixels": top,
                          "lost": round(1 - min(seen[id(i)]) / top, 4) if top else 0.0, "means": means[id(i)]}


def main():
    global counter, rd_replay
    sys.path.insert(0, os.environ["PARETOGPU_ROOT"])
    sys.path.insert(0, os.environ["PARETOGPU_LIB"])
    import rd_loop_counter as counter
    import rd_replay
    with open(os.environ["PARETOGPU_IN"], encoding="utf-8") as f:
        asked = sorted(json.load(f), key=lambda a: min(a["calls"]))
    rp = rd_replay.Replay(*rd_replay.environment())
    written = writes(rp.c)
    done, groups = {}, {}
    full = True
    for item in asked:  # survey, walking forward
        calls = sorted(item["calls"])
        rp.go(calls[0], full)
        full = False
        info = survey(rp.c, calls)
        if isinstance(info, str):
            if not rp.alive():
                raise RuntimeError(f"RenderDoc's server on the device is gone at event {calls[0]}")
            done[item["key"]] = {"error": info}
        elif not info["conts"]:
            done[item["key"]] = {"loops": 0, "nested": [], "pixels": 0, "lost": 0.0, "means": []}
        else:
            info["key"] = item["key"]
            groups.setdefault((int(info["shader"]), info["raw"]), []).append(info)
    total = len(asked)

    def progress(k, of):
        with open(out_path + ".progress", "w") as f:
            f.write(f"{len(done)}/{total} draws, the next {len(infos)}: pass {k}/{of}")
    for infos in groups.values():
        count_group(rp, infos, done, progress, written)
    rp.close()
    with open(out_path, "w") as f:
        json.dump({**done, "_seconds": {k: round(v, 1) for k, v in TIMING.items()}}, f)


try:
    main()
except Exception:
    with open(out_path + ".error", "w") as f:
        f.write(traceback.format_exc())
os._exit(0)
