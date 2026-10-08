"""The Frame Debugger snapshot of the open editor (cs/ParetoGpuFrame.cs, plan item K1.1).

capture() runs ParetoGpuFrame.cs in the open editor (Unity CLI run_script) and waits for <out>/frame.json: the raw
Frame Debugger data of every event of the frame the Game view shows (its resolution, the current quality level).
"""
import json
import os
import re
import time

from paretogpu.adapters.unity.cli import UnityError, editor_ready, errors_of, run_script, script

SCRIPT = script("ParetoGpuFrame.cs")


class SnapshotError(UnityError):
    code = "snapshot"


def capture(project, out, timeout=1800, max_events=0, progress=print, on_step=None):
    """Snapshot the frame of the open editor into <out>/frame.json; returns the parsed raw data.
    on_step(done, total, note): events read so far."""
    project, out = os.path.abspath(project), os.path.abspath(out)
    on_step = on_step or (lambda done, total=None, note=None: None)
    if not editor_ready(project, allow_play=True):
        raise SnapshotError(f"the editor of {project} does not answer Unity CLI or is busy (compiling, importing): "
                            "the snapshot needs the open editor with the frame in the Game view")
    os.makedirs(out, exist_ok=True)
    config = os.path.join(out, "frame_config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"out": out, "max_events": max_events}, f)
    d = run_script(project, SCRIPT, "ParetoGpuFrame.Start", [config], 60000, 120)
    res = (d.get("data") or {}).get("result") or {}
    if res.get("result") != "started":
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise SnapshotError(f"could not start: {res.get('result') or diag or errors_of(d)}")
    t, last = time.time(), None
    paths = {k: os.path.join(out, f"frame.{k}") for k in ("json", "error", "progress")}
    while time.time() - t < timeout:
        if os.path.exists(paths["error"]):
            with open(paths["error"], encoding="utf-8", errors="replace") as f:
                raise SnapshotError(f.read()[-3000:])
        if os.path.exists(paths["json"]):
            time.sleep(0.2)
            with open(paths["json"], encoding="utf-8") as f:
                raw = json.load(f)
            n = len(raw.get("events") or [])
            on_step(n, n, note="done")  # Unity reports every 25 events: the last count is never shown
            return raw
        if os.path.exists(paths["progress"]):
            with open(paths["progress"], encoding="utf-8", errors="replace") as f:
                p = f.read().strip()
            if p != last:
                if progress:
                    progress(f"  {p}")
                m = re.fullmatch(r"events (\d+)/(\d+)", p)
                if m:
                    on_step(int(m.group(1)), int(m.group(2)))
                else:
                    on_step(None, note=p)
            last = p
        time.sleep(0.5)
    raise SnapshotError(f"no result after {timeout} s (progress: {last})")
