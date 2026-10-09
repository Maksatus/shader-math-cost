"""Frame snapshot (plan items K1.1, K1.3): `python -m paretogpu frame`.

The open editor's frame is captured with RenderDoc (adapters/renderdoc), its events are read with the Frame Debugger
(adapters/unity/frame.py: <out>/frame.json) and turned into <out>/frame_events.json (core/events.py): one record per
event with the shader variant, vertices, render target, frame stage and the pixels of its RenderDoc calls.

The frame is whatever the Game view shows in Play Mode (an Edit Mode frame is refused: no game code runs in it):
its resolution and the current quality level.
"""
import json
import os
import shutil
from collections import Counter

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.adapters.renderdoc import RenderDocError
from paretogpu.adapters.unity import frame as unity_frame
from paretogpu.adapters.unity.frame import SnapshotError
from paretogpu.core.events import match, normalize, pixel_count
from paretogpu.features.common import fail
from paretogpu.features.spec import Arg, Command
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE


RD_MIN_MATCHED = 0.5  # share of the draws and dispatches a RenderDoc capture must hold to be the game frame
# A capture of the Game view can hold only the editor UI (the repaint did not render the cameras: see
# ParetoGpuRenderDoc.cs, state 1): it is told by matching its calls and taken again. Its size tells nothing: the
# game frame of a small scene is as small. Since the capture waits for renderViewCallNeededInOnGUI this is a safety net.
RD_ATTEMPTS = 5


def renderdoc_pixels(project, out, rd, events, rep=CONSOLE):
    """Counters of the RenderDoc capture -> ev["rd"] and the pixels of every event. A capture without the game
    frame is taken again (Play Mode is still paused: the same frame); if none of RD_ATTEMPTS is the frame,
    SnapshotError: the snapshot was asked to be exact and must not fall back to diff pixels silently."""
    want = sum(1 for e in events if e["kind"] in ("draw", "compute"))
    rdc = os.path.join(out, "frame.rdc")
    for attempt in range(1, RD_ATTEMPTS + 1):
        rep.phase("rd_counters")
        rep.log("  renderdoc counters")
        if os.path.exists(rdc):
            os.remove(rdc)
        try:
            shutil.move(rd["capture"], rdc)  # keep the capture next to the snapshot (not in git)
            path = rdc
        except OSError:
            path = rd["capture"]
        acts = rdoc.counters(path, os.path.join(out, "rd_actions.json"))
        matched, missing, mismatched = match(events, acts["actions"], acts.get("counters"))
        if not want or matched >= RD_MIN_MATCHED * want:
            break
        calls = sum(1 for a in acts["actions"] if "draw" in a["kinds"] or "dispatch" in a["kinds"])
        why = (f"the RenderDoc capture is not the game frame: {matched} of {want} events found in it "
               f"({calls} calls, mostly the editor UI)")
        if os.path.exists(rd["capture"]) and os.path.abspath(rd["capture"]) != rdc:
            os.remove(rd["capture"])
        if attempt == RD_ATTEMPTS:
            raise SnapshotError(f"{why}, {RD_ATTEMPTS} attempts. Make the Game view visible (not hidden behind "
                                "another tab or a minimized window) and take the snapshot again", "rd_not_game_frame")
        rep.log(f"  {why}: capturing again ({attempt + 1}/{RD_ATTEMPTS})")
        rep.phase("rd_capture")
        rd = rdoc.capture(project, out)
    for ev in events:
        ev["pixel_count"], ev["pixel_method"] = pixel_count(ev)
    return {"capture": path, "matched": matched, "missing": missing, "mismatched": mismatched,
            "counters": acts["counters"], "attempts": attempt}


def run(project, out, timeout=1800, max_events=0, rep=CONSOLE, renderdoc=True):
    """Snapshot the frame: capture it with RenderDoc, read its events with the Frame Debugger (Play Mode is paused
    for both and set back after) and take the pixels of every event from its PSInvocations. renderdoc=False only
    for tests: the events then have no pixels."""
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
            # Play Mode stays paused until the capture is checked: a capture again is of the same frame
            meta["renderdoc"] = renderdoc_pixels(project, out, rd, events, rep)
    finally:
        if rd:
            rdoc.resume(project, was_paused)
    with open(os.path.join(out, "frame_events.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({**meta, "events": events}, f, indent=1, ensure_ascii=False)
    return meta, events


def run_command(args):
    out = args.out or workspace.new_snapshot_dir(args.project, args.suffix)
    try:
        meta, events = run(args.project, out, args.timeout, args.max_events)
    except (SnapshotError, RenderDocError) as e:
        fail(f"snapshot failed: {e}", e)
    draws = [e for e in events if e["kind"] == "draw"]
    print(f"Unity {meta['unity']} ({meta['graphics_api']}, quality {meta['quality']}, "
          f"{'play' if meta['play_mode'] else 'edit'} mode): {len(events)} events in {meta['seconds']} s")
    stages = Counter(e["stage"] for e in events)
    print("stages: " + ", ".join(f"{s} {n}" for s, n in stages.most_common()))
    variants = {(e["shader"], e["pass"], tuple(e["keywords"])) for e in draws}
    print(f"{len(draws)} draws, {len(variants)} shader variants, "
          f"{sum(e['vertices'] for e in draws)} vertices, {len({e['rt']['name'] for e in events})} render targets")
    methods = Counter(e["pixel_method"] for e in draws)
    stage_px = Counter()
    for e in draws:
        stage_px[e["stage"]] += e["pixel_count"] or 0
    print("pixels by stage: " + ", ".join(f"{s} {n:,}" for s, n in stage_px.most_common())
          + "  (" + ", ".join(f"{m} {n}" for m, n in methods.most_common()) + ")")
    if meta.get("renderdoc"):
        r = meta["renderdoc"]
        print(f"renderdoc: {r['matched']} events matched, {r['missing']} without calls -> {r['capture']}")
        if r.get("mismatched"):
            print(f"  WARNING: {r['mismatched']} draws whose RenderDoc calls do not add up to the Frame Debugger index "
                  "count (ev['rd']['count_mismatch']): the call order went astray, their counters may be another draw's")
        lost = {"ps_invocations", "vs_invocations", "cs_invocations"} - set(r.get("counters") or [])
        if lost:
            print(f"  WARNING: the capture has no {', '.join(sorted(lost))}: those counts fall back to other sources")
    print(f"-> {os.path.join(out, 'frame_events.json')}")
    return 0


FRAME = Command(
    "frame", "snapshot the frame of the open Unity editor: RenderDoc (loaded in the editor: Game tab -> Load RenderDoc) "
             "for the pixels and vertices, the Frame Debugger for the shader variant of every event",
    [Arg("--project", required=True, help="Unity project folder (its editor must be open)"),
     Arg("--out", help="output folder (default: paretogpu/out/frame_<project>_<time>, not in git)"),
     Arg("--suffix", help="appended to the snapshot folder name: frame_<project>_<time>_<suffix>"),
     Arg("--timeout", type=int, default=1800, help="seconds"),
     Arg("--max-events", type=int, default=0, help="stop after N events (0 = all)")],
    run_command, phases=lambda v: ["rd_capture", "snapshot", "rd_counters"])
