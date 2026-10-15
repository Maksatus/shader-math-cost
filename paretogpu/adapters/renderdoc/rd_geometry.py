# Runs inside qrenderdoc --python. In: PARETOGPU_RDC, PARETOGPU_DEVICE (rd_replay.py), PARETOGPU_IN [[eid, instances]]
# (draws). Out: PARETOGPU_OUT {eid: {"tris", "offset",
# "viewport": [x, y, w, h], "scissor": [x, y, w, h] or None, "depth_clip", "bias": [constant, clamp, slope] or None,
# "depth_format", "size": [w, h], "samples", "cull", "front_ccw", "depth": id or 0, "ztest": on, "zfunc",
# "zwrite"} or {"error"}} and <out>.bin: float32 clip-space positions (x, y, z, w), 3 per triangle after culling is
# left to core/quads; <out>.progress, traceback -> <out>.error.
import json
import os
import struct
import sys
import traceback

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]


def triangles(controller, inst, topology):
    """-> bytes of the instance's triangles (48 bytes each), or an error string."""
    m = controller.GetPostVSData(inst, 0, rd.MeshDataStage.VSOut)
    if m.vertexResourceId == rd.ResourceId.Null() or not m.numIndices:
        return b""
    data = bytes(controller.GetBufferData(m.vertexResourceId, m.vertexByteOffset, 0))
    stride = m.vertexByteStride
    if m.indexResourceId != rd.ResourceId.Null() and m.indexByteStride in (2, 4):
        raw = bytes(controller.GetBufferData(m.indexResourceId, m.indexByteOffset, m.numIndices * m.indexByteStride))
        idx = struct.unpack(f"<{m.numIndices}{'H' if m.indexByteStride == 2 else 'I'}",
                            raw[:m.numIndices * m.indexByteStride])
    else:
        idx = range(m.numIndices)
    if topology == "TriangleList":
        tris = [idx[k:k + 3] for k in range(0, len(idx) - 2, 3)]
    elif topology == "TriangleStrip":
        tris = [(idx[k], idx[k + 1], idx[k + 2]) if k % 2 == 0 else (idx[k + 1], idx[k], idx[k + 2])
                for k in range(len(idx) - 2)]
    else:
        return f"topology {topology}"
    out = bytearray()
    for t in tris:
        for v in t:
            out += data[v * stride:v * stride + 16]
    return bytes(out)


def main():
    sys.path.insert(0, os.environ["PARETOGPU_LIB"])
    import rd_replay
    with open(os.environ["PARETOGPU_IN"], encoding="utf-8") as f:
        asked = sorted(json.load(f))
    rp = rd_replay.Replay(*rd_replay.environment())
    meta, offset, full = {}, 0, True
    with open(out_path + ".bin", "wb") as blob:
        for n, (eid, instances) in enumerate(asked):
            rp.go(eid, full)
            full = False
            c = rp.c
            pipe, vk = c.GetPipelineState(), c.GetVulkanPipelineState()
            topology = str(pipe.GetPrimitiveTopology()).split(".")[-1]
            body, err = bytearray(), None
            for inst in range(max(1, instances)):
                t = triangles(c, inst, topology)
                if isinstance(t, str):
                    err = t
                    break
                body += t
            if err:
                meta[eid] = {"error": err}
                continue
            vp, sc = pipe.GetViewport(0), pipe.GetScissor(0)
            outs = [t.resource for t in pipe.GetOutputTargets() if t.resource != rd.ResourceId.Null()]
            depth = pipe.GetDepthTarget().resource
            target = outs[0] if outs else depth
            tex = next((t for t in c.GetTextures() if t.resourceId == target), None)
            dtex = next((t for t in c.GetTextures() if t.resourceId == depth), None)
            ds = vk.depthStencil
            meta[eid] = {"tris": len(body) // 48, "offset": offset, "viewport": [vp.x, vp.y, vp.width, vp.height],
                         "scissor": [sc.x, sc.y, sc.width, sc.height] if sc.enabled else None,
                         "depth_clip": bool(vk.rasterizer.depthClipEnable),
                         "bias": [vk.rasterizer.depthBias, vk.rasterizer.depthBiasClamp,
                                  vk.rasterizer.slopeScaledDepthBias] if vk.rasterizer.depthBiasEnable else None,
                         "depth_format": dtex.format.Name() if dtex else None,
                         "size": [tex.width, tex.height] if tex else None, "samples": vk.multisample.rasterSamples,
                         "cull": str(vk.rasterizer.cullMode).split(".")[-1], "front_ccw": bool(vk.rasterizer.frontCCW),
                         "depth": int(depth) if depth != rd.ResourceId.Null() else 0,
                         "ztest": bool(ds.depthTestEnable), "zfunc": str(ds.depthFunction).split(".")[-1],
                         "zwrite": bool(ds.depthWriteEnable)}
            blob.write(body)
            offset += len(body) // 48
            if n % 20 == 0:
                with open(out_path + ".progress", "w") as f:
                    f.write(f"{n}/{len(asked)}")
    rp.close()
    with open(out_path, "w") as f:
        json.dump(meta, f)


try:
    main()
except Exception:
    with open(out_path + ".error", "w") as f:
        f.write(traceback.format_exc())
os._exit(0)
