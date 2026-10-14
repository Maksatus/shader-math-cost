# Runs inside qrenderdoc --python (RenderDoc's UI python, not the normal interpreter). In: capture PARETOGPU_RDC,
# PARETOGPU_IN [{"eid", "pixels"}]. Out: PARETOGPU_OUT {eid: {"loops": [[loop, endloop]], "nested": [bool], "covered",
# "tried", "samples": [[iterations per outer loop]], "seconds"} or {"error"}}; <out>.progress, traceback -> <out>.error.
import json
import os
import random
import re
import time
import traceback

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]
DRAWN = b"\x3c"  # the high byte of 1.0 as a half float: the alpha of the overlay where the call draws (0.5 elsewhere)
DENSE = 0.02     # coverage share from which pixels are picked by random tries instead of a full list


def ok(r):
    return getattr(r, "code", r) == rd.ResultCode.Succeeded


def _instructions(dis):
    out = []
    for line in dis.splitlines():
        m = re.match(r"\s*(\d+):\s+([a-z_0-9]+)\b\s*(.*)$", line)
        if m:
            out.append((int(m.group(1)), m.group(2), [x.strip() for x in m.group(3).split(",") if x.strip()]))
    return out


def _reg(operand):
    """'r0.xxxx' / '-r0.x' -> 'r0.x'; None for non-registers."""
    m = re.match(r"^-?(r\d+)\.([xyzw])[xyzw]*$", operand)
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _constant(code, start, end):
    """Constant trip count: `mov r, l(c)` before, `r` vs a literal + breakc at the top, `iadd r, r, l(k)` its only
    write. Such loops are left out as the GLSL/SPIR-V side leaves them (HLSLcc emits the same pattern)."""
    at = {i: k for k, (i, _, _) in enumerate(code)}
    head = [c for c in code if start < c[0] < end][:4]
    brk = next((c for c in head if c[1] in ("breakc_nz", "breakc_z")), None)
    if not brk or not brk[2]:
        return False
    flag = _reg(brk[2][0])
    cmp_ = next((c for c in head if c[0] < brk[0] and c[2] and _reg(c[2][0]) == flag
                 and c[1] in ("ige", "ilt", "uge", "ult", "ieq", "ine", "ge", "lt")), None)
    if not cmp_ or len(cmp_[2]) != 3:
        return False
    a, b = cmp_[2][1], cmp_[2][2]
    counter = _reg(a) if b.startswith("l(") else _reg(b) if a.startswith("l(") else None
    if not counter:
        return False
    before = [c for c in code[:at[start]] if c[2] and _reg(c[2][0]) == counter]
    if not before or before[-1][1] != "mov" or not before[-1][2][1].startswith("l("):
        return False
    writes = [c for c in code if start < c[0] < end and c[2] and _reg(c[2][0]) == counter]
    return bool(writes) and all(c[1] == "iadd" and len(c[2]) == 3 and _reg(c[2][1]) == counter
                                and c[2][2].startswith("l(") for c in writes)


def outer_loops(dis):
    """-> ([(loop, endloop)] of outer dynamic loops in code order, [holds a dynamic loop], [its continues])."""
    code = _instructions(dis)
    stack, loops = [], []
    for i, op, _ in code:
        if op == "loop":
            stack.append({"start": i, "parent": stack[-1] if stack else None, "continues": []})
        elif op == "endloop" and stack:
            lp = stack.pop()
            lp["end"] = i
            loops.append(lp)
        elif op.startswith("continue") and stack:
            stack[-1]["continues"].append(i)
    for lp in loops:
        lp["dynamic"] = not _constant(code, lp["start"], lp["end"])

    def owner(lp):
        p = lp["parent"]
        while p is not None and not p["dynamic"]:
            p = p["parent"]
        return p
    outer = sorted((lp for lp in loops if lp["dynamic"] and owner(lp) is None), key=lambda lp: lp["start"])
    nested = [any(x["dynamic"] and owner(x) is lp for x in loops) for lp in outer]
    return [(lp["start"], lp["end"]) for lp in outer], nested, [lp["continues"] for lp in outer]


def covered(controller, pipe, k, seed):
    """-> (pixels drawn, k random [(x, y)]) from the Drawcall overlay of the first colour target (alpha 1 where the call
    draws, 0.5 elsewhere; includes pixels a later depth test hides: a small bias). Scans alpha high bytes only."""
    targets = [t for t in pipe.GetOutputTargets() if t.resource != rd.ResourceId.Null()]
    if not targets:
        return 0, []
    rt = targets[0].resource
    tex = next((t for t in controller.GetTextures() if t.resourceId == rt), None)
    if tex is None:
        return 0, []
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
        return 0, []
    hi = bytes(data)[7::8]
    count = hi.count(DRAWN)
    rng = random.Random(seed)
    if count >= DENSE * len(hi):
        picked, seen, tries = [], set(), 0
        while len(picked) < min(k, count) and tries < 200 * k:
            tries += 1
            i = rng.randrange(len(hi))
            if hi[i] == DRAWN[0] and i not in seen:
                seen.add(i)
                picked.append(i)
    else:
        found = [m.start() for m in re.finditer(re.escape(DRAWN), hi)]
        picked = rng.sample(found, min(k, len(found)))
    return count, [(i % w, i // w) for i in picked]


def iterations(controller, x, y, loops, continues):
    """-> [iterations per outer loop] or None (no fragment there). An iteration = a jump back to the loop top by its
    endloop or its own continue; a pass that leaves by break does not count."""
    trace = controller.DebugPixel(x, y, rd.DebugPixelInputs())
    if trace is None or trace.debugger is None:
        return None
    ran, back, prev = {}, [0] * len(loops), None
    own = {c: k for k, cs in enumerate(continues) for c in cs}
    try:
        while True:
            states = controller.ContinueDebug(trace.debugger)
            if not states:
                break
            for s in states:
                ran[s.nextInstruction] = ran.get(s.nextInstruction, 0) + 1
                k = own.get(prev)
                if k is not None and s.nextInstruction in (loops[k][0], loops[k][0] + 1):
                    back[k] += 1
                prev = s.nextInstruction
    finally:
        controller.FreeTrace(trace)
    return [ran.get(end, 0) + b for (_, end), b in zip(loops, back)]


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
        loops, nested, continues = outer_loops(controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), refl,
                                                                            "DXBC"))
        n_px, pool = covered(controller, pipe, 4 * n, eid) if loops else (0, [])
        samples, tried = [], 0
        for x, y in pool:
            if len(samples) >= n:
                break
            tried += 1
            it = iterations(controller, x, y, loops, continues)
            if it is not None:
                samples.append(it)
        res[eid] = {"loops": loops, "nested": nested, "covered": n_px, "tried": tried, "samples": samples,
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
