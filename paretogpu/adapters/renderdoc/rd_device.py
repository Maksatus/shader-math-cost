# Runs inside RenderDoc for Arm GPUs' qrenderdoc --python (not the normal interpreter). In: PARETOGPU_IN {"serial",
# "app": "package/activity", "trigger": file whose appearance means "capture now", "wait": seconds instead (None: the
# trigger), "capture": local .rdc, "shaders": folder for <hash>.<vert|frag|comp>.spv, "adb", "phase": "capture" |
# "replay"}. Two processes: restarting the device's server in the one that captured crashes qrenderdoc now and then.
# Out: PARETOGPU_OUT, capture: {"capture"}; replay: {"device", "serial", "api", "capture", "screen", "actions",
# "draws", "textures", "usage", "discard_error"}; <out>.progress, traceback -> <out>.error.
# Replays on the device: an Android capture does not replay on a desktop GPU (ASTC, AHardwareBuffer).
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from array import array
from types import SimpleNamespace

import renderdoc as rd

sys.path.insert(0, os.environ.get("PARETOGPU_LIB") or os.path.dirname(os.path.abspath(__file__)))
import rd_spirv  # noqa: E402

out_path = os.environ["PARETOGPU_OUT"]

BLEND = {"Zero": "Zero", "One": "One", "SrcCol": "SrcColor", "InvSrcCol": "OneMinusSrcColor", "DstCol": "DstColor",
         "InvDstCol": "OneMinusDstColor", "SrcAlpha": "SrcAlpha", "InvSrcAlpha": "OneMinusSrcAlpha",
         "DstAlpha": "DstAlpha", "InvDstAlpha": "OneMinusDstAlpha"}
KINDS = (("draw", rd.ActionFlags.Drawcall), ("dispatch", rd.ActionFlags.Dispatch), ("clear", rd.ActionFlags.Clear),
         ("copy", rd.ActionFlags.Copy), ("resolve", rd.ActionFlags.Resolve), ("present", rd.ActionFlags.Present),
         ("begin_pass", rd.ActionFlags.BeginPass), ("end_pass", rd.ActionFlags.EndPass))
STAGES = ((rd.ShaderStage.Vertex, "vert"), (rd.ShaderStage.Pixel, "frag"))
WAITING = "the app is running: waiting for the capture"
REOPEN = 200  # calls between reopenings: the device's replay server leaks up to ~4 MB a call, lmkd kills it


def ok(r):
    return getattr(r, "code", r) == rd.ResultCode.Succeeded


def step(text):
    with open(out_path + ".progress", "w", encoding="utf-8") as f:
        f.write(text)


def connect(ctrl, url):
    r = ctrl.StartRemoteServer(url)
    if not ok(r):
        raise RuntimeError(f"cannot start the RenderDoc server on the device: {r}")
    for _ in range(30):
        r, remote = rd.CreateRemoteServerConnection(url)
        if ok(r):
            return remote
        time.sleep(1)
    raise RuntimeError(f"no connection to the RenderDoc server on the device: {r}")


def capture(ctrl, url, remote, job, serial):
    """Copies the capture to job["capture"]."""
    step("starting the app")
    res = remote.ExecuteAndInject(job["app"], "", "", [], rd.GetDefaultCaptureOptions())
    if not ok(res.result):
        raise RuntimeError(f"cannot start {job['app']} through RenderDoc: {res.result}")
    target = None
    for _ in range(60):
        target = rd.CreateTargetControl(url, res.ident, "paretogpu", True)
        if target is not None:
            break
        time.sleep(1)
    if target is None:
        raise RuntimeError("the app started but RenderDoc cannot reach it (is it a development build?)")
    step(WAITING)
    t0 = time.time()

    def ready():
        if job.get("wait") is None:
            return os.path.exists(job.get("trigger") or "")
        return time.time() - t0 >= job["wait"]
    while not ready():
        msg = target.ReceiveMessage(None)
        if msg.type == rd.TargetControlMessageType.Disconnected:
            raise RuntimeError("the app closed before the capture (do not touch the device)")
        time.sleep(0.2)
    step("capturing")
    target.TriggerCapture(1)
    cap, t0 = None, time.time()
    while cap is None and time.time() - t0 < 120:
        msg = target.ReceiveMessage(None)
        if msg.type == rd.TargetControlMessageType.NewCapture:
            cap = msg.newCapture
        elif msg.type == rd.TargetControlMessageType.Disconnected:
            raise RuntimeError("the app closed during the capture (do not touch the device)")
        time.sleep(0.1)
    if cap is None:
        raise RuntimeError("no capture in 120 s")
    step("copying the capture")
    target.CopyCapture(cap.captureId, job["capture"])
    t0, copied = time.time(), False
    while not copied and time.time() - t0 < 600:
        copied = target.ReceiveMessage(None).type == rd.TargetControlMessageType.CaptureCopied
        time.sleep(0.1)
    target.Shutdown()
    subprocess.run([job["adb"], "-s", serial, "shell", "am", "force-stop", job["app"].split("/")[0]],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not copied or not os.path.exists(job["capture"]):
        raise RuntimeError("the capture was not copied from the device in 600 s")


def leaf_actions(controller):
    """-> [(action as plain data, path)]: they outlive the controller (read_frame reopens the capture)."""
    sf = controller.GetStructuredFile()
    out = []

    def walk(items, path):
        for a in items:
            if a.children:
                walk(a.children, path + [a.GetName(sf)])
            else:
                out.append((SimpleNamespace(
                    eventId=a.eventId, name=a.GetName(sf), flags=a.flags, numIndices=a.numIndices,
                    numInstances=a.numInstances, indexOffset=a.indexOffset,
                    dispatchDimension=list(a.dispatchDimension)), "/".join(path)))
    walk(controller.GetRootActions(), [])
    return sorted(out, key=lambda x: x[0].eventId)


def write_shader(refl, short, folder, cache):
    rid = int(refl.resourceId)
    if rid not in cache:
        raw = bytes(refl.rawBytes)
        h = hashlib.sha1(raw + refl.entryPoint.encode()).hexdigest()[:16]
        name = f"{h}.{short}.spv"
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            with open(path + ".tmp", "wb") as f:
                f.write(raw)
            os.replace(path + ".tmp", path)
        cache[rid] = {"hash": h, "file": name, "entry": refl.entryPoint, "bytes": len(raw)}
    return cache[rid]


def block_bytes(controller, pipe, stage, refl, cache):
    """-> {"@set.binding": base64} of the uniform blocks a stage reads (loop bounds for core/loops.spirv_bounds)."""
    out = {}
    for i, cb in enumerate(refl.constantBlocks):
        if not cb.bufferBacked:
            continue
        used = pipe.GetConstantBlock(stage, i, 0)
        desc = getattr(used, "descriptor", used)
        res = getattr(desc, "resource", None) or getattr(desc, "resourceId", rd.ResourceId.Null())
        if res == rd.ResourceId.Null():
            continue
        size = cb.byteSize or min(desc.byteSize, 65536)
        key = (int(res), desc.byteOffset, size)
        if key not in cache:
            cache[key] = base64.b64encode(bytes(controller.GetBufferData(res, desc.byteOffset, size))).decode()
        out[f"@{cb.fixedBindSetOrSpace}.{cb.fixedBindNumber}"] = cache[key]
    return out


def unique_vertices(controller, pipe, a):
    ib = pipe.GetIBuffer()
    if ib.resourceId == rd.ResourceId.Null() or ib.byteStride not in (2, 4) or not a.numIndices:
        return None
    data = controller.GetBufferData(ib.resourceId, ib.byteOffset + a.indexOffset * ib.byteStride,
                                    a.numIndices * ib.byteStride)
    idx = array("H" if ib.byteStride == 2 else "I")
    idx.frombytes(bytes(data)[:len(data) - len(data) % ib.byteStride])
    seen = set(idx)
    seen.discard(0xFFFF if ib.byteStride == 2 else 0xFFFFFFFF)
    return len(seen)


def lost(remote, event, done, total):
    """After the device's server dies the API keeps answering with empty objects, no exception."""
    why = ("RenderDoc's server on the device is gone (the driver crashed replaying it)" if not remote.Ping()
           else "the replay returned no pipeline")
    raise RuntimeError(f"the replay on the device failed at event {event} (call {done}/{total}): {why}; the frame "
                       "is not written")


def read_frame(controller, remote, job, names, textures, reopen):
    """-> (actions, draws, discarding {frag shader id: (ResourceId, entry, SPIR-V, [events])}, controller).
    reopen(controller) -> a fresh controller of the same capture."""
    samples = {}
    if rd.GPUCounter.SamplesPassed in controller.EnumerateCounters():
        for r in controller.FetchCounters([rd.GPUCounter.SamplesPassed]):
            samples[r.eventId] = r.value.u64
    leaves = leaf_actions(controller)
    actions, draws, cache, blocks, discarding = [], {}, {}, {}, {}
    work = [a for a, _ in leaves if a.flags & (rd.ActionFlags.Drawcall | rd.ActionFlags.Dispatch)]
    done = 0
    for a, path in leaves:
        name = a.name
        kinds = [k for k, f in KINDS if a.flags & f]
        actions.append({"event": a.eventId, "name": name, "path": path, "flags": int(a.flags), "kinds": kinds,
                        "indices": a.numIndices, "instances": a.numInstances})
        if not ({"draw", "dispatch"} & set(kinds)):
            continue
        done += 1
        if done % REOPEN == 0:
            step(f"reading the frame: call {done}/{len(work)}, reopening the capture")
            controller = reopen(controller)
        if done % 20 == 1:
            step(f"reading the frame: call {done}/{len(work)}")
        controller.SetFrameEvent(a.eventId, False)
        pipe = controller.GetPipelineState()
        d = {"event": a.eventId}
        if "dispatch" in kinds:
            refl = pipe.GetShaderReflection(rd.ShaderStage.Compute)
            if refl is None:
                lost(remote, a.eventId, done, len(work))
            d["shaders"] = {"comp": {**write_shader(refl, "comp", job["shaders"], cache),
                                     "name": names.get(int(refl.resourceId))}}
            d["blocks"] = block_bytes(controller, pipe, rd.ShaderStage.Compute, refl, blocks)
            d["groups"] = list(a.dispatchDimension)
            d["group_size"] = list(refl.dispatchThreadsDimension)
            draws[a.eventId] = d
            continue
        sh, ub = {}, {}
        for stage, short in STAGES:
            refl = pipe.GetShaderReflection(stage)
            if refl is not None:
                sh[short] = {**write_shader(refl, short, job["shaders"], cache),
                             "name": names.get(int(refl.resourceId))}
                ub.update(block_bytes(controller, pipe, stage, refl, blocks))
                if short == "frag":
                    rid = pipe.GetShader(stage)
                    if int(rid) not in discarding and rd_spirv.discards(bytes(refl.rawBytes)):
                        discarding[int(rid)] = (rid, refl.entryPoint, bytes(refl.rawBytes), [])
                    if int(rid) in discarding:
                        discarding[int(rid)][3].append(a.eventId)
                        d["discards"] = True
        if "vert" not in sh:
            lost(remote, a.eventId, done, len(work))
        outs = [t.resource for t in pipe.GetOutputTargets() if t.resource != rd.ResourceId.Null()]
        depth = pipe.GetDepthTarget().resource
        target = outs[0] if outs else depth
        tex = textures.get(int(target))
        blends = pipe.GetColorBlends()
        b = blends[0] if blends else None
        vk = controller.GetVulkanPipelineState()
        ds = vk.depthStencil
        meshes = [names.get(int(v.resourceId)) or "" for v in pipe.GetVBuffers()
                  if v.resourceId != rd.ResourceId.Null()]
        d.update({
            "shaders": sh, "blocks": ub, "pipeline": names.get(int(pipe.GetGraphicsPipelineObject())),
            "rt": {"id": int(target), "name": names.get(int(target)) or "", "width": tex.width if tex else None,
                   "height": tex.height if tex else None, "samples": tex.msSamp if tex else 1, "count": len(outs),
                   "backbuffer": bool(tex and tex.creationFlags & rd.TextureCategory.SwapBuffer)},
            "blend": (f"{BLEND.get(str(b.colorBlend.source).split('.')[-1], 'One')} "
                      f"{BLEND.get(str(b.colorBlend.destination).split('.')[-1], 'Zero')}" if b and b.enabled
                      else "One Zero"),
            "write_mask": b.writeMask if b else 0,
            "ztest": str(ds.depthFunction).split(".")[-1] if ds.depthTestEnable else "Disabled",
            "zwrite": bool(ds.depthTestEnable and ds.depthWriteEnable),
            "cull": str(vk.rasterizer.cullMode).split(".")[-1],
            "meshes": list(dict.fromkeys(m for m in meshes if m)),
            "unique_vertices": unique_vertices(controller, pipe, a) if a.flags & rd.ActionFlags.Indexed else None,
            "indexed": bool(a.flags & rd.ActionFlags.Indexed), "samples_passed": samples.get(a.eventId)})
        draws[a.eventId] = d
    return actions, draws, discarding, controller


def shaded_samples(controller, discarding):
    """-> ({event: samples}, error): Samples Passed recounted with discards removed (rd_spirv.py). Bias: fragments no
    longer discarded stay in the depth buffer, so a later discarding draw behind them counts a little less."""
    if not discarding:
        return {}, None
    if rd.ShaderEncoding.SPIRV not in controller.GetTargetShaderEncodings():
        return {}, "no_spirv_edit"
    replaced = []
    try:
        for rid, entry, raw, events in discarding.values():
            new = rd_spirv.without_discard(raw)
            if new is None:
                continue
            sid, _ = controller.BuildTargetShader(entry, rd.ShaderEncoding.SPIRV, new, rd.ShaderCompileFlags(),
                                                  rd.ShaderStage.Pixel)
            if sid == rd.ResourceId.Null():
                continue
            controller.ReplaceResource(rid, sid)
            replaced.append((rid, sid, events))
        if not replaced:
            return {}, "not_built"
        values = {r.eventId: r.value.u64 for r in controller.FetchCounters([rd.GPUCounter.SamplesPassed])}
    finally:
        for rid, sid, _ in replaced:
            controller.RemoveReplacement(rid)
            controller.FreeTargetResource(sid)
    return {e: values[e] for _, _, events in replaced for e in events if e in values}, None


def read_textures(controller, names):
    """As rd_memory.py; "native": the formats are the device's own."""
    textures, usage = [], {}
    for t in controller.GetTextures():
        rid = int(t.resourceId)
        cat = t.creationFlags
        textures.append({
            "id": rid, "name": names.get(rid, ""), "width": t.width, "height": t.height, "depth": t.depth,
            "mips": t.mips, "arrays": t.arraysize, "samples": t.msSamp, "format": t.format.Name(),
            "bytes": t.byteSize, "cube": bool(t.cubemap), "native": True,
            "color_target": bool(cat & rd.TextureCategory.ColorTarget),
            "depth_target": bool(cat & rd.TextureCategory.DepthTarget),
            "swapchain": bool(cat & rd.TextureCategory.SwapBuffer)})
        usage[rid] = [[u.eventId, str(u.usage).split(".")[-1]] for u in controller.GetUsage(t.resourceId)]
    return textures, usage


def write(answer):
    with open(out_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(answer, f)
    os.replace(out_path + ".tmp", out_path)


def main():
    with open(os.environ["PARETOGPU_IN"], encoding="utf-8") as f:
        job = json.load(f)
    ctrl = rd.GetDeviceProtocolController("adb")
    devs = ctrl.GetDevices()
    serial = job.get("serial") or (devs[0] if devs else None)
    if not serial or serial not in devs:
        raise RuntimeError(f"no Android device {job.get('serial') or ''} (adb sees: {', '.join(devs) or 'none'})")
    url = ctrl.GetProtocolName() + "://" + serial
    remote = connect(ctrl, url)
    if job["phase"] == "capture":
        capture(ctrl, url, remote, job, serial)
        write({"capture": job["capture"]})
        remote.ShutdownConnection()
        return
    step("sending the capture to the device")
    device_path = remote.CopyCaptureToRemote(job["capture"], None)
    step("replaying on the device")
    def open_capture(old=None):
        if old is not None:
            remote.CloseCapture(old)
        r, c = remote.OpenCapture(rd.RemoteServer.NoPreference, device_path, rd.ReplayOptions(), None)
        if not ok(r):
            raise RuntimeError(f"cannot replay the capture on the device: {r}")
        return c
    controller = open_capture()
    api = str(controller.GetAPIProperties().pipelineType).split(".")[-1]
    if api != "Vulkan":
        raise RuntimeError(f"the capture is {api}: a device frame needs a Vulkan build (its SPIR-V and pipeline state)")
    os.makedirs(job["shaders"], exist_ok=True)
    names = {int(x.resourceId): x.name for x in controller.GetResources()}
    textures = {int(t.resourceId): t for t in controller.GetTextures()}
    actions, draws, discarding, controller = read_frame(controller, remote, job, names, textures, open_capture)
    step("reading the textures")
    tex, usage = read_textures(controller, names)
    screen = next(([t["width"], t["height"]] for t in tex if t["swapchain"]), None)
    answer = {"device": ctrl.GetFriendlyName(url), "serial": serial, "api": api,
              "capture": job.get("capture"), "screen": screen, "actions": actions,
              "draws": draws, "textures": tex, "usage": usage, "discard_error": None}
    write(answer)  # the frame is kept if a replay below crashes
    step(f"counting the fragments of the draws whose shaders discard ({len(discarding)} shaders)")
    try:
        shaded, answer["discard_error"] = shaded_samples(controller, discarding)
        for e, v in shaded.items():
            draws[e]["samples_shaded"] = v
    except Exception as e:
        answer["discard_error"] = "crashed" if not remote.Ping() else f"{type(e).__name__}: {e}"[:300]
    write(answer)
    if answer["discard_error"] != "crashed":
        remote.CloseCapture(controller)
    try:
        remote.ShutdownServerAndConnection()
    except Exception:
        pass  # the server is gone after a crash


try:
    main()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
os._exit(0)
