"""RenderDoc reference for the frame snapshot (plan item K1.3).

capture(): adapters/unity/cs/ParetoGpuRenderDoc.cs pauses Play Mode, puts the Frame Debugger on its last event (it re-renders
the whole game frame inside the Game view repaint) and records that repaint with RenderDoc — the same frame the
Frame Debugger snapshot reads right after. counters(): rd_counters.py inside qrenderdoc gives every API call
with its marker path and GPU counters (PSInvocations = how many times the pixel shader ran); core/events.match()
gives every Frame Debugger event its calls. loop_iterations(): rd_loops.py inside qrenderdoc debugs a few pixels of
given calls and counts how many times every outer dynamic loop of their pixel shader ran (plan K1.10, stage C).
memory(): rd_memory.py inside qrenderdoc lists every texture of the capture and how every call used it (plan K2.3).
"""
import hashlib
import json
import os
import shutil
import subprocess
import winreg

from paretogpu.adapters.unity import bridge
from paretogpu.model.errors import ParetoError

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "ParetoGpuRenderDoc.cs"
COUNTERS = os.path.join(HERE, "rd_counters.py")
LOOPS = os.path.join(HERE, "rd_loops.py")
MEMORY = os.path.join(HERE, "rd_memory.py")


class RenderDocError(ParetoError):
    code = "renderdoc"


def qrenderdoc():
    """qrenderdoc.exe: RENDERDOC_DIR, the registered install (the one Unity loads), or Program Files."""
    cands = []
    if os.environ.get("RENDERDOC_DIR"):
        cands.append(os.path.join(os.environ["RENDERDOC_DIR"], "qrenderdoc.exe"))
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes\RenderDoc.RDCCapture.1\DefaultIcon") as k:
            cands.append(os.path.expandvars(winreg.QueryValueEx(k, "")[0]).split(",")[0].strip('"'))
    except OSError:
        pass
    cands += [r"C:\Program Files\RenderDoc\qrenderdoc.exe", shutil.which("qrenderdoc") or ""]
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise RenderDocError("qrenderdoc.exe not found: install RenderDoc or set RENDERDOC_DIR")


def capture(project, out, timeout=300):
    """Capture the current game frame; Play Mode stays paused (call resume() after the other snapshots).
    Returns {"capture": path, "bytes", "was_paused": bool}."""
    return bridge.call(project, SCRIPT, "Start", out, "renderdoc", {"pause": True}, RenderDocError, timeout)


def resume(project, paused):
    """Set the Play Mode pause back."""
    bridge.run(project, SCRIPT, "Pause", ["true" if paused else "false"], RenderDocError)


def _script(script, capture_path, out_json, timeout, what):
    """Run a qrenderdoc --python script on the capture; returns the JSON it wrote."""
    for p in (out_json, out_json + ".error"):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_OUT=out_json)
    subprocess.run([qrenderdoc(), "--python", script], env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            raise RenderDocError(f.read()[-2000:])
    if not os.path.exists(out_json):
        raise RenderDocError(f"qrenderdoc wrote no {what}")
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


def counters(capture_path, out_json, timeout=900):
    """Per-call GPU counters of the capture (runs qrenderdoc --python)."""
    return _script(COUNTERS, capture_path, out_json, timeout, "counters")


def memory(capture_path, out_json, timeout=900):
    """{"textures": [...], "usage": {texture id: [[event id, usage]]}} of the capture (rd_memory.py)."""
    return _script(MEMORY, capture_path, out_json, timeout, "textures")


def signature(path):
    """What identifies a capture file without reading all of it: its size, time and first megabyte."""
    st = os.stat(path)
    with open(path, "rb") as f:
        head = hashlib.sha1(f.read(1 << 20)).hexdigest()[:16]
    return f"{st.st_size}|{int(st.st_mtime)}|{head}"


def free_memory():
    """Bytes of physical memory free now (Windows), or None."""
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = Status()
    st.dwLength = ctypes.sizeof(Status)
    try:
        return st.ullAvailPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
    except (AttributeError, OSError):
        return None


MAX_WORKERS = 4
PROCESS_MEMORY = 128 << 20  # one qrenderdoc replaying a game frame: this + twice the capture (measured: 252 MB, 102 MB)


def workers_for(capture_path, calls):
    """How many qrenderdoc to run at once: one per core group of 4, within the free memory, at most MAX_WORKERS."""
    free = free_memory()
    per = PROCESS_MEMORY + 2 * os.path.getsize(capture_path)
    by_memory = max(1, int(free // per)) if free else 1
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 4) // 4, by_memory, calls))


def loop_iterations(capture_path, asked, out_json, timeout=3600, workers=None):
    """{call event id: {"loops", "covered", "tried", "samples": [[iterations per outer loop]]} or {"error"}} of the
    calls asked ([{"eid", "pixels"}]): rd_loops.py inside qrenderdoc, the calls in frame order split over `workers`
    processes at once (default workers_for(): each replays the capture itself)."""
    asked = sorted(asked, key=lambda a: a["eid"])
    k = workers or workers_for(capture_path, len(asked))
    parts = [asked[i * len(asked) // k:(i + 1) * len(asked) // k] for i in range(k)]
    runs = []
    for i, part in enumerate(p for p in parts if p):
        out = f"{out_json}.{i}"
        for x in (out, out + ".error", out + ".progress"):
            if os.path.exists(x):
                os.remove(x)
        with open(out + ".in", "w", encoding="utf-8") as f:
            json.dump(part, f)
        env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_IN=out + ".in", PARETOGPU_OUT=out)
        runs.append((out, subprocess.Popen([qrenderdoc(), "--python", LOOPS], env=env, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)))
    res = {}
    try:
        for out, proc in runs:
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                raise RenderDocError(f"qrenderdoc did not count the loops in {timeout} s") from None
            if os.path.exists(out + ".error"):
                with open(out + ".error", encoding="utf-8") as f:
                    raise RenderDocError(f.read()[-2000:])
            if not os.path.exists(out):
                raise RenderDocError("qrenderdoc wrote no loop iterations")
            with open(out, encoding="utf-8") as f:
                res.update({int(e): v for e, v in json.load(f).items()})
    finally:
        for _, proc in runs:
            if proc.poll() is None:
                proc.kill()
    return res
