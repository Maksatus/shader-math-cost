"""The Frame Debugger snapshot of the open editor (cs/ParetoGpuFrame.cs, plan item K1.1).

capture() runs ParetoGpuFrame.cs in the open editor (bridge.py) and waits for <out>/frame.json: the raw Frame Debugger
data of every event of the frame the Game view shows (its resolution, the current quality level).
"""
import os
import re

from paretogpu.adapters.unity import bridge
from paretogpu.adapters.unity.cli import UnityError

SCRIPT = "ParetoGpuFrame.cs"


class SnapshotError(UnityError):
    code = "snapshot"


def capture(project, out, timeout=1800, max_events=0, log=None, on_step=None):
    """Snapshot the frame of the open editor into <out>/frame.json; returns the parsed raw data.
    log(text): the progress Unity writes; on_step(done, total, note): events read so far."""
    project, out = os.path.abspath(project), os.path.abspath(out)
    on_step = on_step or (lambda done, total=None, note=None: None)
    bridge.require_editor(project, SnapshotError, "the snapshot needs the open editor with the frame in the Game view")

    def progress(text):
        if log:
            log(f"  {text}")
        m = re.fullmatch(r"events (\d+)/(\d+)", text)
        if m:
            on_step(int(m.group(1)), int(m.group(2)))
        else:
            on_step(None, note=text)

    raw = bridge.call(project, SCRIPT, "Start", out, "frame", {"max_events": max_events}, SnapshotError, timeout,
                      progress)
    n = len(raw.get("events") or [])
    on_step(n, n, note="done")  # Unity reports every 25 events: the last count is never shown
    return raw
