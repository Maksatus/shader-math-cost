"""The capture replayed by every rd_*.py that replays (inside qrenderdoc's python): on the desktop, or with a serial on
that Android device by RenderDoc for Arm (an Android capture does not replay on a desktop GPU: ETC2, AHardwareBuffer).
The device's replay server grows with every SetFrameEvent (~0.2 MB alone, MBs with pipeline state or MSAA texture
reads; 600 moves got it killed by lmkd at move ~590 of rd_draws) until lmkd kills it: the capture is reopened every
REOPEN moves, the shader swaps set again after it."""
import os
import time

import renderdoc as rd

REOPEN = 150


def ok(r):
    return getattr(r, "code", r) == rd.ResultCode.Succeeded


def connect(serial, start=True):
    """-> (remote server connection, url) on the device; start: (re)starts its server first."""
    ctrl = rd.GetDeviceProtocolController("adb")
    devs = ctrl.GetDevices()  # also fills RenderDoc's device list
    if serial not in devs:
        raise RuntimeError(f"adb does not see {serial} (it sees: {', '.join(devs) or 'none'})")
    url = ctrl.GetProtocolName() + "://" + serial
    r = ctrl.StartRemoteServer(url) if start else None
    if start and not ok(r):
        raise RuntimeError(f"cannot start the RenderDoc server on the device: {r}")
    for _ in range(30):
        r, remote = rd.CreateRemoteServerConnection(url)
        if ok(r):
            return remote, url
        time.sleep(1)
    raise RuntimeError(f"no connection to the RenderDoc server on the device: {r}")


def state(controller, rdc):
    """-> rd_vulkan.Frame of the capture: every call's state from its structured data (read from the local file), in
    the replay's event order."""
    import rd_vulkan
    events = []

    def walk(items):
        for a in items:
            events.extend((e.eventId, e.chunkIndex) for e in a.events)
            walk(a.children)
    walk(controller.GetRootActions())
    cap = rd.OpenCaptureFile()
    if not ok(cap.OpenFile(rdc, "", None)):
        raise RuntimeError("cannot open capture")
    try:
        return rd_vulkan.frame(cap.GetStructuredData(), events)
    finally:
        cap.Shutdown()


class Replay:

    def __init__(self, rdc, serial=None, remote=None, step=None):
        """serial: replayed on that device (remote: an open connection to its server); step(text): progress."""
        self.moved, self.swaps, self.remote, self.step = 0, {}, None, step or (lambda text: None)
        self._output, self._pinged = None, time.time()
        if not serial:
            cap = rd.OpenCaptureFile()
            if not ok(cap.OpenFile(rdc, "", None)):
                raise RuntimeError("cannot open capture")
            r, self.c = cap.OpenCapture(rd.ReplayOptions(), None)
            if not ok(r):
                raise RuntimeError(f"cannot replay capture: {r}")
            self._cap = cap
            return
        self.remote = remote or connect(serial)[0]
        self.step("sending the capture to the device")
        self.path = self.remote.CopyCaptureToRemote(rdc, None)
        self.step("replaying on the device")
        self.c = self._open()

    def _open(self):
        r, c = self.remote.OpenCapture(rd.RemoteServer.NoPreference, self.path, rd.ReplayOptions(), None)
        if not ok(r):
            raise RuntimeError(f"cannot replay the capture on the device: {r}")
        return c

    def alive(self):
        return self.remote is None or ok(self.remote.Ping())

    def keepalive(self):
        """Pings the device's server during local work: it drops a client silent for 15 s."""
        if self.remote is not None and time.time() - self._pinged > 3:
            self.remote.Ping()
            self._pinged = time.time()

    def go(self, eid, full=False):
        """Moves to eid; a reopening replays from the start with the swaps set again."""
        if self.remote is not None and self.moved >= REOPEN:
            if not self.alive():
                raise RuntimeError("RenderDoc's server on the device is gone (out of memory or crashed)")
            self.step("reopening the capture")
            swaps = {k: v[:3] for k, v in self.swaps.items()}
            self.unswap()
            self._drop_output()
            self.remote.CloseCapture(self.c)
            self.c, self.moved, full = self._open(), 0, True
            self.swap(swaps)
        self.c.SetFrameEvent(eid, full)
        self.moved += 1

    def output(self):
        """-> a texture output of the open replay (for overlays); a reopening makes a new one."""
        if self._output is None:
            self._output = self.c.CreateOutput(rd.CreateHeadlessWindowingData(64, 64), rd.ReplayOutputType.Texture)
        return self._output

    def _drop_output(self):
        if self._output is not None:
            self._output.Shutdown()
            self._output = None

    def swap(self, swaps):
        """swaps {shader ResourceId: (entry, SPIR-V[, rd.ShaderStage, Pixel if left out])} -> {that id: error} of the
        ones that did not build."""
        failed = {}
        for original, (entry, spirv, *stage) in swaps.items():
            stage = stage[0] if stage else rd.ShaderStage.Pixel
            sid, errors = self.c.BuildTargetShader(entry, rd.ShaderEncoding.SPIRV, spirv, rd.ShaderCompileFlags(), stage)
            if sid == rd.ResourceId.Null():
                failed[original] = f"the shader did not build: {errors}"
                continue
            self.c.ReplaceResource(original, sid)
            self.swaps[original] = (entry, spirv, stage, sid)
        return failed

    def unswap(self):
        for original, (_, _, _, sid) in self.swaps.items():
            self.c.RemoveReplacement(original)
            self.c.FreeTargetResource(sid)
        self.swaps = {}

    def close(self):
        self._drop_output()
        if self.remote is None:
            self.c.Shutdown()
            self._cap.Shutdown()
            return
        if self.alive():
            self.remote.CloseCapture(self.c)
            self.remote.ShutdownServerAndConnection()


def textures(controller, native):
    """-> (textures, {texture id: [[event id, usage]]}) of the capture (core/memory); native: the formats are the
    device's own (a capture replayed on its device), not the editor's."""
    names = {int(x.resourceId): x.name for x in controller.GetResources()}
    out, usage = [], {}
    for t in controller.GetTextures():
        rid = int(t.resourceId)
        cat = t.creationFlags
        out.append({
            "id": rid, "name": names.get(rid, ""), "width": t.width, "height": t.height, "depth": t.depth,
            "mips": t.mips, "arrays": t.arraysize, "samples": t.msSamp, "format": t.format.Name(),
            "bytes": t.byteSize, "cube": bool(t.cubemap), "native": native,
            "color_target": bool(cat & rd.TextureCategory.ColorTarget),
            "depth_target": bool(cat & rd.TextureCategory.DepthTarget),
            "swapchain": bool(cat & rd.TextureCategory.SwapBuffer)})
        usage[rid] = [[u.eventId, str(u.usage).split(".")[-1]] for u in controller.GetUsage(t.resourceId)]
    return out, usage


def environment():
    """-> (capture, serial or None) of a script's job: PARETOGPU_RDC, PARETOGPU_DEVICE."""
    return os.environ["PARETOGPU_RDC"], os.environ.get("PARETOGPU_DEVICE") or None
