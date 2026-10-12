# Runs inside RenderDoc's UI python (qrenderdoc --python rd_loops.py), not in the normal interpreter (plan K1.10,
# stage C). Opens the capture in PARETOGPU_RDC; PARETOGPU_IN is [{"eid", "pixels"}] (RenderDoc draw calls); writes
# PARETOGPU_OUT: {eid: {"loops": [[loop, endloop]], "covered", "tried", "samples": [[iterations per outer loop]]}}.
#
# For every call: the outer loops of its pixel shader (DXBC `loop` ... `endloop` in code order: the GLSL and SPIR-V
# the prices come from are made from the same DXBC by HLSLcc, so their outer loops are in this order too), the pixels
# the call covers (the Drawcall overlay: alpha 1 where the call draws, 0.5 elsewhere), and on `pixels` random ones of
# them the pixel shader debugged: the iterations of an outer loop are how many times its `endloop` ran (a pass that
# leaves by a break does not reach it). The overlay also holds pixels a later depth test hides: a small bias.
import json
import os
import random
import re
import time
import traceback

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]
ONE = 0x3C00  # 1.0 as a half float: the alpha of the overlay where the call draws


def ok(r):
    return getattr(r, "code", r) == rd.ResultCode.Succeeded


def outer_loops(dis):
    """[(loop instruction, its endloop instruction)] of the outer loops of a DXBC disassembly, in code order."""
    stack, out = [], []
    for line in dis.splitlines():
        m = re.match(r"\s*(\d+):\s+(loop|endloop)\b", line)
        if not m:
            continue
        i = int(m.group(1))
        if m.group(2) == "loop":
            stack.append(i)
        elif stack:
            start = stack.pop()
            if not stack:
                out.append((start, i))
    return out


def covered(controller, pipe):
    """The pixels the current call draws: [(x, y)] of the Drawcall overlay of its first colour target."""
    targets = [t for t in pipe.GetOutputTargets() if t.resource != rd.ResourceId.Null()]
    if not targets:
        return []
    rt = targets[0].resource
    tex = next((t for t in controller.GetTextures() if t.resourceId == rt), None)
    if tex is None:
        return []
    w, h = tex.width, tex.height
    out = controller.CreateOutput(rd.CreateHeadlessWindowingData(w, h), rd.ReplayOutputType.Texture)
    try:
        disp = rd.TextureDisplay()
        disp.resourceId = rt
        disp.overlay = rd.DebugOverlay.Drawcall
        out.SetTextureDisplay(disp)
        out.Display()
        data = controller.GetTextureData(out.GetDebugOverlayTexID(), rd.Subresource())
    finally:
        out.Shutdown()
    if not w * h or len(data) != 8 * w * h:  # RGBA16F expected
        return []
    alpha = memoryview(bytes(data)).cast("H")[3::4]
    return [(i % w, i // w) for i, a in enumerate(alpha) if a == ONE]


def iterations(controller, x, y, loops):
    """[iterations of every outer loop] of the pixel shader at (x, y), or None (no fragment of the call there)."""
    trace = controller.DebugPixel(x, y, rd.DebugPixelInputs())
    if trace is None or trace.debugger is None:
        return None
    ran = {}
    try:
        while True:
            states = controller.ContinueDebug(trace.debugger)
            if not states:
                break
            for s in states:
                ran[s.nextInstruction] = ran.get(s.nextInstruction, 0) + 1
    finally:
        controller.FreeTrace(trace)
    return [ran.get(end, 0) for _, end in loops]


def main():
    with open(os.environ["PARETOGPU_IN"], encoding="utf-8") as f:
        asked = json.load(f)
    cap = rd.OpenCaptureFile()
    if not ok(cap.OpenFile(os.environ["PARETOGPU_RDC"], "", None)):
        raise RuntimeError("cannot open capture")
    r, controller = cap.OpenCapture(rd.ReplayOptions(), None)
    if not ok(r):
        raise RuntimeError(f"cannot replay capture: {r}")
    res = {}
    for item in asked:
        t0 = time.time()
        eid, n = int(item["eid"]), int(item["pixels"])
        controller.SetFrameEvent(eid, True)
        pipe = controller.GetPipelineState()
        refl = pipe.GetShaderReflection(rd.ShaderStage.Pixel)
        if refl is None:
            res[eid] = {"error": "no pixel shader"}
            continue
        loops = outer_loops(controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), refl, "DXBC"))
        px = covered(controller, pipe) if loops else []
        random.seed(eid)
        samples, tried = [], 0
        for x, y in random.sample(px, min(len(px), 4 * n)):
            if len(samples) >= n:
                break
            tried += 1
            it = iterations(controller, x, y, loops)
            if it is not None:
                samples.append(it)
        res[eid] = {"loops": loops, "covered": len(px), "tried": tried, "samples": samples,
                    "seconds": round(time.time() - t0, 2)}
        with open(out_path + ".progress", "w") as f:
            f.write(f"{len(res)}/{len(asked)}")
    controller.Shutdown()
    cap.Shutdown()
    with open(out_path, "w") as f:
        json.dump(res, f)


try:
    main()
except Exception:
    with open(out_path + ".error", "w") as f:
        f.write(traceback.format_exc())
os._exit(0)
