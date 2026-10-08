"""RenderDoc reference for the frame snapshot (plan item K1.3).

capture(): adapters/unity/cs/ParetoGpuRenderDoc.cs pauses Play Mode, puts the Frame Debugger on its last event (it re-renders
the whole game frame inside the Game view repaint) and records that repaint with RenderDoc — the same frame the
Frame Debugger snapshot reads right after. counters(): rd_counters.py inside qrenderdoc gives every API call
with its marker path and GPU counters (PSInvocations = how many times the pixel shader ran); core/events.match()
gives every Frame Debugger event its calls.
"""
import json
import os
import shutil
import subprocess
import time
import winreg

from paretogpu.adapters.unity.cli import code_of, errors_of, run_script, script
from paretogpu.model.errors import ParetoError

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = script("ParetoGpuRenderDoc.cs")
COUNTERS = os.path.join(HERE, "rd_counters.py")


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


def _run_entry(project, entry, args, timeout=120):
    d = run_script(project, SCRIPT, entry, args, timeout * 1000, timeout + 60)
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise RenderDocError(f"{entry}: {diag or res.get('errorDetails') or errors_of(d)}"[:2000], code_of(d))
    return res.get("result")


def capture(project, out, timeout=300):
    """Capture the current game frame; Play Mode stays paused (call resume() after the other snapshots).
    Returns {"capture": path, "was_paused": bool}."""
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    for f in ("renderdoc.json", "renderdoc.error"):
        if os.path.exists(os.path.join(out, f)):
            os.remove(os.path.join(out, f))
    config = os.path.join(out, "renderdoc_config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"out": out, "pause": True}, f)
    started = _run_entry(project, "ParetoGpuRenderDoc.Start", [config])
    if started != "started":
        raise RenderDocError(str(started))
    t = time.time()
    while time.time() - t < timeout:
        if os.path.exists(os.path.join(out, "renderdoc.error")):
            with open(os.path.join(out, "renderdoc.error"), encoding="utf-8", errors="replace") as f:
                raise RenderDocError(f.read()[-2000:])
        if os.path.exists(os.path.join(out, "renderdoc.json")):
            time.sleep(0.2)
            with open(os.path.join(out, "renderdoc.json"), encoding="utf-8") as f:
                return json.load(f)
        time.sleep(0.5)
    raise RenderDocError(f"no capture after {timeout} s")


def resume(project, paused):
    """Set the Play Mode pause back."""
    _run_entry(project, "ParetoGpuRenderDoc.Pause", ["true" if paused else "false"])


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
