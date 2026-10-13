# Runs inside RenderDoc's UI python (qrenderdoc --python rd_memory.py), not in the normal interpreter (plan K2.3).
# Opens the capture in PARETOGPU_RDC, writes PARETOGPU_OUT: every texture of the capture (size, mips, array layers,
# samples, format, bytes, whether it is a colour / depth target) and how every call used it (GetUsage: sampled by a
# stage, written as a colour or depth target, cleared, copied, resolved), as [[event id, usage]].
import json
import os
import traceback

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]


def ok(r):
    return getattr(r, "code", r) == rd.ResultCode.Succeeded


def main():
    cap = rd.OpenCaptureFile()
    if not ok(cap.OpenFile(os.environ["PARETOGPU_RDC"], "", None)):
        raise RuntimeError("cannot open capture")
    if not cap.LocalReplaySupport():
        raise RuntimeError("capture cannot be replayed locally")
    r, controller = cap.OpenCapture(rd.ReplayOptions(), None)
    if not ok(r):
        raise RuntimeError(f"cannot replay capture: {r}")
    names = {int(x.resourceId): x.name for x in controller.GetResources()}
    textures, usage = [], {}
    for t in controller.GetTextures():
        rid = int(t.resourceId)
        cat = t.creationFlags
        textures.append({
            "id": rid, "name": names.get(rid, ""), "width": t.width, "height": t.height, "depth": t.depth,
            "mips": t.mips, "arrays": t.arraysize, "samples": t.msSamp, "format": t.format.Name(),
            "bytes": t.byteSize, "cube": bool(t.cubemap),
            "color_target": bool(cat & rd.TextureCategory.ColorTarget),
            "depth_target": bool(cat & rd.TextureCategory.DepthTarget),
            "swapchain": bool(cat & rd.TextureCategory.SwapBuffer)})
        usage[rid] = [[u.eventId, str(u.usage).split(".")[-1]] for u in controller.GetUsage(t.resourceId)]
    controller.Shutdown()
    cap.Shutdown()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"textures": textures, "usage": usage}, f)


try:
    main()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
os._exit(0)
