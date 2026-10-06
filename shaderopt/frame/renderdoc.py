"""RenderDoc reference for the frame snapshot (plan item K1.3).

capture(): unity/ShaderoptRenderDoc.cs pauses Play Mode, puts the Frame Debugger on its last event (it re-renders
the whole game frame inside the Game view repaint) and records that repaint with RenderDoc — the same frame the
Frame Debugger snapshot reads right after. counters(): frame/rd_counters.py inside qrenderdoc gives every API call
with its marker path and GPU counters (PSInvocations = how many times the pixel shader ran). match(): the calls of
each Frame Debugger event are the next calls with the same marker path, in order.
"""
import json
import os
import re
import shutil
import subprocess
import time
import winreg

from shaderopt.unity import export as unity

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "unity", "ShaderoptRenderDoc.cs")
COUNTERS = os.path.join(HERE, "rd_counters.py")


class RenderDocError(RuntimeError):
    pass


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
    d = unity._cli_json(["command", "run_script", "--project-path", project, "--timeout_ms", str(timeout * 1000),
                         "--file", os.path.abspath(SCRIPT), "--entry", entry, "--args", json.dumps(args)], timeout + 60)
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise RenderDocError(f"{entry}: {diag or res.get('errorDetails') or unity._errors(d)}"[:2000])
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
    started = _run_entry(project, "ShaderoptRenderDoc.Start", [config])
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
    _run_entry(project, "ShaderoptRenderDoc.Pause", ["true" if paused else "false"])


def counters(capture_path, out_json, timeout=900):
    """Per-call GPU counters of the capture (runs qrenderdoc --python)."""
    for p in (out_json, out_json + ".error"):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, SHADEROPT_RDC=capture_path, SHADEROPT_OUT=out_json)
    subprocess.run([qrenderdoc(), "--python", COUNTERS], env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            raise RenderDocError(f.read()[-2000:])
    if not os.path.exists(out_json):
        raise RenderDocError("qrenderdoc wrote no counters")
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


_RP = re.compile(r"\(RP \d+:\d+\) ")


def _norm(path):
    return _RP.sub("", path or "").strip("/")


def match(events, actions):
    """Attach to every draw / compute event its RenderDoc calls:
    ev["rd"] = {calls, events, ps / ps_invocations, vs / vs_invocations, cs_invocations, samples, gpu_ms}.
    The calls of an event are the next calls (draws for draws, dispatches for compute) whose marker path ends with
    the event's path; a Frame Debugger event holds m_DrawCallCount calls (several for an SRP batch).
    Returns (matched events, events without calls)."""
    calls = [a for a in actions if "draw" in a["kinds"] or "dispatch" in a["kinds"]]
    pos, matched, missing = 0, 0, 0
    for ev in events:
        if ev["kind"] not in ("draw", "compute"):
            continue
        want = "dispatch" if ev["kind"] == "compute" else "draw"
        key, n = _norm(ev["path"]), max(1, ev["draw_calls"] or 1)
        got, j = [], pos
        while j < len(calls) and len(got) < n:
            a = calls[j]
            if want in a["kinds"] and (_norm(a["path"]).endswith(key) or (not key and not a["path"])):
                got.append(j)
            elif got:
                break  # the calls of one event are consecutive
            j += 1
        if len(got) != n:
            ev["rd"] = None
            missing += 1
            continue
        pos = got[-1] + 1
        sel = [calls[k] for k in got]
        ps = sum(a.get("ps_invocations") or 0 for a in sel)
        vs = sum(a.get("vs_invocations") or 0 for a in sel)
        cs = sum(a.get("cs_invocations") or 0 for a in sel)
        ev["rd"] = {"calls": n, "events": [a["event"] for a in sel], "ps": ps, "vs": vs,
                    "ps_invocations": ps, "vs_invocations": vs, "cs_invocations": cs,
                    "samples": sum(a.get("samples_passed") or 0 for a in sel),
                    "gpu_ms": round(sum(a.get("gpu_duration") or 0 for a in sel) * 1000, 4)}
        matched += 1
    return matched, missing
