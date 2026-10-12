"""RenderDoc reference for the frame snapshot (plan item K1.3).

capture(): adapters/unity/cs/ParetoGpuRenderDoc.cs pauses Play Mode, puts the Frame Debugger on its last event (it re-renders
the whole game frame inside the Game view repaint) and records that repaint with RenderDoc — the same frame the
Frame Debugger snapshot reads right after. counters(): rd_counters.py inside qrenderdoc gives every API call
with its marker path and GPU counters (PSInvocations = how many times the pixel shader ran); core/events.match()
gives every Frame Debugger event its calls. loop_iterations(): rd_loops.py inside qrenderdoc debugs a few pixels of
given calls and counts how many times every outer dynamic loop of their pixel shader ran (plan K1.10, stage C).
"""
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


def counters(capture_path, out_json, timeout=900):
    """Per-call GPU counters of the capture (runs qrenderdoc --python)."""
    for p in (out_json, out_json + ".error"):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_OUT=out_json)
    subprocess.run([qrenderdoc(), "--python", COUNTERS], env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            raise RenderDocError(f.read()[-2000:])
    if not os.path.exists(out_json):
        raise RenderDocError("qrenderdoc wrote no counters")
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


def loop_iterations(capture_path, asked, out_json, timeout=3600):
    """{call event id: {"loops", "covered", "tried", "samples": [[iterations per outer loop]]} or {"error"}} of the
    calls asked ([{"eid", "pixels"}]): rd_loops.py inside qrenderdoc."""
    for p in (out_json, out_json + ".error", out_json + ".progress"):
        if os.path.exists(p):
            os.remove(p)
    with open(out_json + ".in", "w", encoding="utf-8") as f:
        json.dump(asked, f)
    env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_IN=out_json + ".in", PARETOGPU_OUT=out_json)
    try:
        subprocess.run([qrenderdoc(), "--python", LOOPS], env=env, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RenderDocError(f"qrenderdoc did not count the loops in {timeout} s") from None
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            raise RenderDocError(f.read()[-2000:])
    if not os.path.exists(out_json):
        raise RenderDocError("qrenderdoc wrote no loop iterations")
    with open(out_json, encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}
