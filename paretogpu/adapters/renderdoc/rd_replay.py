"""The capture replayed for rd_loops.py / rd_geometry.py (inside qrenderdoc's python): on the desktop, or with
PARETOGPU_DEVICE (adb serial) on that device by RenderDoc for Arm, reopened every REOPEN frame events moved to (its
replay server leaks per replay and is killed by lmkd, rd_device.py); a shader swap is set again after a reopening."""
import os
import time

import renderdoc as rd

REOPEN = 150


def ok(r):
    return getattr(r, "code", r) == rd.ResultCode.Succeeded


class Replay:

    def __init__(self):
        self.moved, self.swap = 0, None
        if not os.environ.get("PARETOGPU_DEVICE"):
            cap = rd.OpenCaptureFile()
            if not ok(cap.OpenFile(os.environ["PARETOGPU_RDC"], "", None)):
                raise RuntimeError("cannot open capture")
            r, self.c = cap.OpenCapture(rd.ReplayOptions(), None)
            if not ok(r):
                raise RuntimeError(f"cannot replay capture: {r}")
            self.remote, self.close = None, lambda: (self.c.Shutdown(), cap.Shutdown())
            return
        ctrl = rd.GetDeviceProtocolController("adb")
        if os.environ["PARETOGPU_DEVICE"] not in ctrl.GetDevices():  # also fills RenderDoc's device list
            raise RuntimeError(f"adb does not see {os.environ['PARETOGPU_DEVICE']}")
        url = ctrl.GetProtocolName() + "://" + os.environ["PARETOGPU_DEVICE"]
        if not ok(ctrl.StartRemoteServer(url)):
            raise RuntimeError("cannot start the RenderDoc server on the device")
        r = None
        for _ in range(30):
            r, self.remote = rd.CreateRemoteServerConnection(url)
            if ok(r):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"no connection to the RenderDoc server on the device: {r}")
        self.path = self.remote.CopyCaptureToRemote(os.environ["PARETOGPU_RDC"], None)
        self.c = self._open()
        self.close = lambda: (self.remote.CloseCapture(self.c), self.remote.ShutdownServerAndConnection())

    def _open(self):
        r, c = self.remote.OpenCapture(rd.RemoteServer.NoPreference, self.path, rd.ReplayOptions(), None)
        if not ok(r):
            raise RuntimeError(f"cannot replay the capture on the device: {r}")
        return c

    def go(self, eid, full=False):
        """Moves to eid; a reopening replays from the start with the swap set again."""
        if self.remote is not None and self.moved >= REOPEN:
            if not ok(self.remote.Ping()):
                raise RuntimeError("RenderDoc's server on the device is gone (out of memory or crashed)")
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
