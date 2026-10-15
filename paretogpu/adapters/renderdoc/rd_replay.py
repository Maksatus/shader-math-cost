"""The capture replayed by every rd_*.py that replays (inside qrenderdoc's python): on the desktop, or with a serial on
that Android device by RenderDoc for Arm (an Android capture does not replay on a desktop GPU: ASTC, AHardwareBuffer).
The device's replay server leaks up to ~4 MB per SetFrameEvent and lmkd kills it: the capture is reopened every REOPEN
moves, a shader swap set again after it."""
import base64
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


class Replay:

    def __init__(self, rdc, serial=None, remote=None, step=None):
        """serial: replayed on that device (remote: an open connection to its server); step(text): progress."""
        self.moved, self.swap, self.remote, self.step = 0, None, None, step or (lambda text: None)
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

    def go(self, eid, full=False):
        """Moves to eid; a reopening replays from the start with the swap set again."""
        if self.remote is not None and self.moved >= REOPEN:
            if not self.alive():
                raise RuntimeError("RenderDoc's server on the device is gone (out of memory or crashed)")
            self.step("reopening the capture")
            swap = self.swap
            self.unswap()
            self.remote.CloseCapture(self.c)
            self.c, self.moved, full = self._open(), 0, True
            if swap:
                self.set_swap(*swap)
        self.c.SetFrameEvent(eid, full)
        self.moved += 1

    def set_swap(self, original, entry, spirv):
        sid, errors = self.c.BuildTargetShader(entry, rd.ShaderEncoding.SPIRV, spirv, rd.ShaderCompileFlags(),
                                               rd.ShaderStage.Pixel)
        if sid == rd.ResourceId.Null():
            raise ValueError(f"the counting shader did not build: {errors}")
        self.c.ReplaceResource(original, sid)
        self.swap, self.sid = (original, entry, spirv), sid

    def unswap(self):
        if self.swap:
            self.c.RemoveReplacement(self.swap[0])
            self.c.FreeTargetResource(self.sid)
            self.swap = None

    def close(self):
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
