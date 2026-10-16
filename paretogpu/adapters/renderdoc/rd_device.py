# Runs inside RenderDoc for Arm GPUs' qrenderdoc --python (not the normal interpreter). In: PARETOGPU_IN {"serial",
# "app": "package/activity", "trigger": file whose appearance means "capture now", "wait": seconds instead (None: the
# trigger), "capture": local .rdc, "shaders": folder for <hash>.<vert|frag|comp>.spv,
# "knockout": measure the fragment shaders by swapping them, "adb"}, PARETOGPU_LIB, PARETOGPU_ROOT.
# Out: PARETOGPU_OUT {"device", "serial", "api", "capture", "screen", "rotation", "actions", "draws", "textures",
# "usage", "discard_error", "mali", "knockout"}; <out>.progress, traceback -> <out>.error.
import json
import os
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

import renderdoc as rd

sys.path.insert(0, os.environ["PARETOGPU_LIB"])
import rd_replay  # noqa: E402
import rd_spirv  # noqa: E402
import rd_vulkan  # noqa: E402
from rd_replay import ok  # noqa: E402

out_path = os.environ["PARETOGPU_OUT"]

KINDS = (("draw", rd.ActionFlags.Drawcall), ("dispatch", rd.ActionFlags.Dispatch), ("clear", rd.ActionFlags.Clear),
         ("copy", rd.ActionFlags.Copy), ("resolve", rd.ActionFlags.Resolve), ("present", rd.ActionFlags.Present),
         ("begin_pass", rd.ActionFlags.BeginPass), ("end_pass", rd.ActionFlags.EndPass))
WAITING = "the app is running: waiting for the capture"
ROTATION = {1: 0, 2: 90, 4: 180, 8: 270}  # VkSurfaceTransformFlagBitsKHR
KNOCKOUT_REPEATS = 3  # Execution core active varies ±2-3% between replays, issue cycles do not
# Mali counters RenderDoc for Arm gives per render pass (keyed by its vkCmdBeginRenderPass event), summed over the cores
MALI = ("Execution core active cycles", "Arithmetic unit issue cycles", "Load/store unit issue cycles",
        "Texture unit issue cycles", "Varying unit issue cycles", "Fragment threads",
        "Position shader thread invocations", "Varying shader thread invocations", "GPU active cycles")


def step(text):
    with open(out_path + ".progress", "w", encoding="utf-8") as f:
        f.write(text)


def capture(url, remote, job):
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
    step("closing the app")
    target.Shutdown()
    subprocess.run([job["adb"], "-s", job["serial"], "shell", "am", "force-stop", job["app"].split("/")[0]],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not copied or not os.path.exists(job["capture"]):
        raise RuntimeError("the capture was not copied from the device in 600 s")


def leaf_actions(controller):
    """-> [(action as plain data, path)]."""
    sf = controller.GetStructuredFile()
    out = []

    def walk(items, path):
        for a in items:
            if a.children:
                walk(a.children, path + [a.GetName(sf)])
            else:
                out.append((SimpleNamespace(
                    eventId=a.eventId, name=a.GetName(sf), flags=a.flags, numIndices=a.numIndices,
                    numInstances=a.numInstances), "/".join(path)))
    walk(controller.GetRootActions(), [])
    return sorted(out, key=lambda x: x[0].eventId)


def samples_passed(controller):
    if rd.GPUCounter.SamplesPassed not in controller.EnumerateCounters():
        return {}
    return {r.eventId: r.value.u64 for r in controller.FetchCounters([rd.GPUCounter.SamplesPassed])}


def shaders_of(f, stage):
    """-> {module id: (entry, SPIR-V, [draw events])} of the frame's draws' "frag" / "vert" shaders."""
    out = {}
    for e, st in sorted(f.calls.items()):
        if stage in st["stages"] and "comp" not in st["stages"]:
            module, entry = st["stages"][stage]
            out.setdefault(module, (entry, f.modules[module], []))[2].append(e)
    return out


def discarding_of(f):
    return {m: v for m, v in shaders_of(f, "frag").items() if rd_spirv.discards(v[1])}


def read_frame(leaves, f, job, names, textures, samples, discarding):
    """-> (actions, draws) of the answer, with no replay call: the calls' state comes from the capture's structured
    data (rd_vulkan.py), not from moving the replay to every call."""
    tex = {t["id"]: t for t in textures}
    written = {}

    def write(module, entry, short):
        if (module, entry) not in written:
            raw = f.modules[module]
            h, name = rd_vulkan.shader_file(raw, entry, short)
            path = os.path.join(job["shaders"], name)
            if not os.path.exists(path):
                with open(path + ".tmp", "wb") as fh:
                    fh.write(raw)
                os.replace(path + ".tmp", path)
            written[(module, entry)] = {"hash": h, "file": name, "entry": entry, "bytes": len(raw)}
        return written[(module, entry)]
    discards = {e for _, _, events in discarding.values() for e in events}
    actions, draws = [], {}
    for a, path in leaves:
        kinds = [k for k, fl in KINDS if a.flags & fl]
        actions.append({"event": a.eventId, "name": a.name, "path": path, "flags": int(a.flags), "kinds": kinds,
                        "indices": a.numIndices, "instances": a.numInstances})
        if not ({"draw", "dispatch"} & set(kinds)):
            continue
        d = {"event": a.eventId, **rd_vulkan.describe(f.calls[a.eventId], names, tex, write)}
        if "draw" in kinds:
            if a.eventId in discards:
                d["discards"] = True
            d["samples_passed"] = samples.get(a.eventId)
        draws[a.eventId] = d
    return actions, draws


def shaded_samples(controller, discarding):
    """-> ({event: samples}, error): Samples Passed recounted with discards removed (rd_spirv.py). Bias: fragments no
    longer discarded stay in the depth buffer, so a later discarding draw behind them counts a little less."""
    if not discarding:
        return {}, None
    if rd.ShaderEncoding.SPIRV not in controller.GetTargetShaderEncodings():
        return {}, "no_spirv_edit"
    replaced = []
    ids = {int(x.resourceId): x.resourceId for x in controller.GetResources()}
    try:
        for module, (entry, raw, events) in discarding.items():
            rid = ids[module]
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


def _mali(controller):
    have = {controller.DescribeCounter(c).name: c for c in controller.EnumerateCounters()}
    return [(n, have[n]) for n in MALI if n in have]


def _fetch(controller, counters):
    slot = {int(c): i for i, (_, c) in enumerate(counters)}
    passes = {}
    for r in controller.FetchCounters([c for _, c in counters]):
        v = r.value.u64 if r.value.u64 < 1 << 62 else r.value.d
        passes.setdefault(r.eventId, [None] * len(counters))[slot[int(r.counter)]] = v
    return passes


def _median(controller, counters):
    runs = [_fetch(controller, counters) for _ in range(KNOCKOUT_REPEATS)]
    out = {}
    for e in runs[0]:
        cols = zip(*(r.get(e) or [None] * len(counters) for r in runs))
        out[e] = [sorted(x)[len(x) // 2] if None not in x else None for x in cols]
    return out, {e: max(r[e][0] or 0 for r in runs if e in r) - min(r[e][0] or 0 for r in runs if e in r)
                 for e in runs[0]}


def mali_counters(controller):
    """-> {"names", "passes": {begin event: [value per name]}} of the MALI counters this GPU has."""
    counters = _mali(controller)
    if not counters:
        return {"names": [], "passes": {}, "error": "no_mali_counters"}
    passes = _fetch(controller, counters)
    return {"names": [n for n, _ in counters], "passes": passes, "error": None if passes else "empty"}


# no "varyings zeroed" knockout: the driver folds constant varyings into the fragment shader, which then costs less too
KNOCKOUTS = (("frag", "colour", rd_spirv.zero_outputs), ("vert", "draw", rd_spirv.off_screen))


def knockout(rp, f, step):
    """Mali counters per pass with each shader swapped in turn for a stub (rd_spirv): "colour" = the fragment shader's
    colour zeroed, "draw" = the vertex shader's position off screen (its draws cost nothing); median of KNOCKOUT_REPEATS replays. -> {"names", "base", "spread": {pass: exec max - min of the base
    replays}, "shaders": [{"stage", "mode", "file", "events", "passes" | "error"}]}."""
    c = rp.c
    counters = _mali(c)
    ids = {int(x.resourceId): x.resourceId for x in c.GetResources()}
    jobs = [(stage, mode, make, module, v) for stage, mode, make in KNOCKOUTS
            for module, v in sorted(shaders_of(f, stage).items())]
    step(f"shader cost: 0/{len(jobs)}")
    base, spread = _median(c, counters)
    out = []
    for k, (stage, mode, make, module, (entry, raw, events)) in enumerate(jobs):
        step(f"shader cost: {k + 1}/{len(jobs)}")
        row = {"stage": stage, "mode": mode, "file": rd_vulkan.shader_file(raw, entry, stage)[1], "events": events}
        out.append(row)
        stub = make(raw)
        if stub is None:
            row["error"] = "no_output"
            continue
        sid, errors = c.BuildTargetShader(entry, rd.ShaderEncoding.SPIRV, stub, rd.ShaderCompileFlags(),
                                          rd.ShaderStage.Pixel if stage == "frag" else rd.ShaderStage.Vertex)
        if sid == rd.ResourceId.Null():
            row["error"] = f"not built: {errors}"[:300]
            continue
        c.ReplaceResource(ids[module], sid)
        try:
            row["passes"] = _median(c, counters)[0]
        finally:
            c.RemoveReplacement(ids[module])
            c.FreeTargetResource(sid)
    return {"names": [n for n, _ in counters], "base": base, "spread": spread, "shaders": out}


def rotation(controller):
    """-> degrees of the swapchain's pre-transform: the frame is drawn rotated by them on a phone held otherwise."""
    sf = controller.GetStructuredFile()
    for ch in sf.chunks:
        if ch.name != "vkCreateSwapchainKHR":
            continue
        info = next((ch.GetChild(i) for i in range(ch.NumChildren()) if ch.GetChild(i).name == "CreateInfo"), None)
        pre = info and next((info.GetChild(i) for i in range(info.NumChildren())
                             if info.GetChild(i).name == "preTransform"), None)
        if pre is None:
            raise RuntimeError("vkCreateSwapchainKHR of the capture has no preTransform")
        if pre.AsInt() not in ROTATION:
            raise RuntimeError(f"unexpected swapchain preTransform {pre.AsString()} ({pre.AsInt()})")
        return ROTATION[pre.AsInt()]
    raise RuntimeError("the capture has no vkCreateSwapchainKHR: its screen orientation is unknown")


def write(answer):
    with open(out_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(answer, f)
    os.replace(out_path + ".tmp", out_path)


def main():
    with open(os.environ["PARETOGPU_IN"], encoding="utf-8") as f:
        job = json.load(f)
    remote, url = rd_replay.connect(job["serial"])
    capture(url, remote, job)
    step("connecting to the replay server")
    remote.ShutdownConnection()  # the connection that started the app no longer replays
    remote, url = rd_replay.connect(job["serial"], start=False)
    rp = rd_replay.Replay(job["capture"], job["serial"], remote, step)
    api = str(rp.c.GetAPIProperties().pipelineType).split(".")[-1]
    if api != "Vulkan":
        raise RuntimeError(f"the capture is {api}: a device frame needs a Vulkan build (its SPIR-V and pipeline state)")
    os.makedirs(job["shaders"], exist_ok=True)
    # the device's server drops a client silent for 15 s: replay calls first, the local work after
    names = {int(x.resourceId): None if x.autogeneratedName else x.name for x in rp.c.GetResources()}
    turn = rotation(rp.c)
    tex, usage = rd_replay.textures(rp.c, True)
    leaves = leaf_actions(rp.c)
    samples = samples_passed(rp.c)
    step("reading the frame's calls from the capture")
    f = rd_replay.state(rp.c, job["capture"])
    discarding = discarding_of(f)
    step(f"counting the fragments of the draws whose shaders discard ({len(discarding)} shaders)")
    shaded, discard_error = {}, None
    try:
        shaded, discard_error = shaded_samples(rp.c, discarding)
    except Exception as e:
        discard_error = "crashed" if not rp.alive() else f"{type(e).__name__}: {e}"[:300]
    step("Mali counters of the render passes")
    try:  # last: on some drivers (client) a counter replay crashes the device's server
        mali = mali_counters(rp.c)
    except Exception as e:
        mali = {"names": [], "passes": {}, "error": "crashed" if not rp.alive() else f"{type(e).__name__}: {e}"[:300]}
    cost = None
    if job.get("knockout") and mali["passes"] and rd.ShaderEncoding.SPIRV in rp.c.GetTargetShaderEncodings():
        try:
            cost = knockout(rp, f, step)
        except Exception as e:
            cost = {"error": "crashed" if not rp.alive() else f"{type(e).__name__}: {e}"[:300]}
    model = rd.GetDeviceProtocolController("adb").GetFriendlyName(url)
    rp.close()
    step("writing the frame")
    actions, draws = read_frame(leaves, f, job, names, tex, samples, discarding)
    for e, v in shaded.items():
        draws[e]["samples_shaded"] = v
    screen = next(([t["width"], t["height"]] for t in tex if t["swapchain"]), None)
    write({"device": model, "serial": job["serial"], "api": api, "capture": job["capture"],
           "screen": screen[::-1] if screen and turn in (90, 270) else screen, "rotation": turn,
           "actions": actions, "draws": draws, "textures": tex, "usage": usage, "discard_error": discard_error,
           "mali": mali, "knockout": cost})


try:
    main()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
os._exit(0)
