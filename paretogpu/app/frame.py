"""Frame snapshot: RenderDoc capture + Frame Debugger events of the Game view in Play Mode (an Edit Mode frame is
refused: no game code runs in it)."""
import os
import shutil
import struct
import subprocess

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.adapters.unity import frame as unity_frame
from paretogpu.adapters.unity.frame import SnapshotError
from paretogpu.app import frame_draws
from paretogpu.core.events import match, normalize, pixel_count
from paretogpu.model.frame import Snapshot
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


RD_MIN_MATCHED = 0.5  # share of the draws and dispatches a RenderDoc capture must hold to be the game frame
# A Game view capture can hold only the editor UI (ParetoGpuRenderDoc.cs state 1): told by matching calls, not by size.
# Safety net only: the capture already waits for renderViewCallNeededInOnGUI.
RD_ATTEMPTS = 5
CAPTURE = "frame.rdc"


def renderdoc_pixels(project, out, rd, events, rep=CONSOLE):
    """A capture without the game frame is retaken (Play Mode still paused: same frame); after RD_ATTEMPTS raises
    SnapshotError rather than silently falling back to diff pixels."""
    want = sum(1 for e in events if e["kind"] in ("draw", "compute"))
    rdc = os.path.join(out, CAPTURE)
    for attempt in range(1, RD_ATTEMPTS + 1):
        rep.phase("rd_counters")
        rep.log("  renderdoc counters")
        if os.path.exists(rdc):
            os.remove(rdc)
        shutil.move(rd["capture"], rdc)
        acts = rdoc.counters(rdc, os.path.join(out, "rd_actions.json"))
        matched, missing, mismatched = match(events, acts["actions"], acts.get("counters"))
        if not want or matched >= RD_MIN_MATCHED * want:
            break
        calls = sum(1 for a in acts["actions"] if "draw" in a["kinds"] or "dispatch" in a["kinds"])
        why = (f"the RenderDoc capture is not the game frame: {matched} of {want} events found in it "
               f"({calls} calls, mostly the editor UI)")
        if attempt == RD_ATTEMPTS:
            raise SnapshotError(f"{why}, {RD_ATTEMPTS} attempts. Make the Game view visible (not hidden behind "
                                "another tab or a minimized window) and take the snapshot again", "rd_not_game_frame")
        rep.log(f"  {why}: capturing again ({attempt + 1}/{RD_ATTEMPTS})")
        rep.phase("rd_capture")
        rd = rdoc.capture(project, out)
    for ev in events:
        ev["pixel_count"], ev["pixel_method"] = pixel_count(ev)
    return {"capture": CAPTURE, "replay": None, "matched": matched, "missing": missing, "mismatched": mismatched,
            "counters": acts["counters"], "attempts": attempt}


def save_thumbnail(capture_path, folder, rotation=0, rep=CONSOLE):
    """Mini preview of the measured frame for the report and the snapshot list; a failure only logs."""
    try:
        rdoc.thumbnail(capture_path, os.path.join(folder, workspace.THUMB), rotation=rotation)
    except (rdoc.RenderDocError, OSError, ValueError, struct.error, subprocess.TimeoutExpired) as e:
        rep.log(f"  no frame preview: {str(e).strip().splitlines()[-1] if str(e).strip() else e}")


def run(project, out, timeout=1800, max_events=0, rep=CONSOLE, renderdoc=True) -> Snapshot:
    """renderdoc=False only for tests: the events then have no pixels."""
    out = os.path.abspath(out)
    rd = None
    if renderdoc:
        rep.phase("rd_capture")
        rep.log("  renderdoc capture")
        rd = rdoc.capture(project, out)
    was_paused = (rd or {}).get("was_paused", False)
    try:
        rep.phase("snapshot")
        raw = unity_frame.capture(project, out, timeout, max_events, rep.log, rep.step)
        events = normalize(raw)
        meta = {k: v for k, v in raw.items() if k != "events"}
        if rd:
            # Play Mode stays paused until the capture is checked, so a retake is of the same frame
            meta["renderdoc"] = renderdoc_pixels(project, out, rd, events, rep)
            save_thumbnail(os.path.join(out, CAPTURE), out, rep=rep)
    finally:
        if rd:
            rdoc.resume(project, was_paused)
    if rd:
        rep.phase("rd_draws")
        rep.log("  renderdoc draws: post-VS geometry and loop trip counts")
        rdoc.draws(os.path.join(out, CAPTURE), os.path.join(out, frame_draws.FILE), on_progress=rep.log)
    return {**meta, "events": events}
