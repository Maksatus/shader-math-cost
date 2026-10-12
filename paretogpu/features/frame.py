"""Frame snapshot (plan items K1.1, K1.3): `python -m paretogpu frame`.

The frame of the open editor is captured and read by app/frame.py into <out>/frame_events.json: one record per event
with the shader variant, vertices, render target, frame stage and the pixels of its RenderDoc calls. The frame is
whatever the Game view shows in Play Mode: its resolution, the current quality level and render pipeline asset.
"""
import json
import os
from collections import Counter

from paretogpu.app import frame
from paretogpu.core import profile
from paretogpu.features.common import fail
from paretogpu.features.spec import Arg, Command
from paretogpu.model.errors import ParetoError
from paretogpu.store import workspace


def run_command(args):
    out = args.out or workspace.new_snapshot_dir(args.project, args.suffix)
    try:
        snap = frame.run(args.project, out, args.timeout, args.max_events)
    except ParetoError as e:
        fail(f"snapshot failed: {e}", e)
    with open(os.path.join(out, workspace.SNAPSHOT_FILE), "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
    events = snap["events"]
    draws = [e for e in events if e["kind"] == "draw"]
    print(f"Unity {snap['unity']} ({snap['graphics_api']}, quality {snap['quality']}, "
          f"{'play' if snap['play_mode'] else 'edit'} mode): {len(events)} events in {snap['seconds']} s")
    print(profile.summary(profile.describe(snap, profile.frame_size(
        [{"rt_size": [e["rt"]["width"], e["rt"]["height"]], "pixels": e["pixel_count"], "stage": e["stage"]}
         for e in draws]))))
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
    if snap.get("renderdoc"):
        r = snap["renderdoc"]
        print(f"renderdoc: {r['matched']} events matched, {r['missing']} without calls -> {r['capture']}")
        if r.get("mismatched"):
            print(f"  WARNING: {r['mismatched']} draws whose RenderDoc calls do not add up to the Frame Debugger index "
                  "count (ev['rd']['count_mismatch']): the call order went astray, their counters may be another draw's")
        lost = {"ps_invocations", "vs_invocations", "cs_invocations"} - set(r.get("counters") or [])
        if lost:
            print(f"  WARNING: the capture has no {', '.join(sorted(lost))}: those counts fall back to other sources")
    print(f"-> {os.path.join(out, workspace.SNAPSHOT_FILE)}")
    return 0


FRAME = Command(
    "frame", "snapshot the frame of the open Unity editor: RenderDoc (loaded in the editor: Game tab -> Load RenderDoc) "
             "for the pixels and vertices, the Frame Debugger for the shader variant of every event",
    [Arg("--project", required=True, help="Unity project folder (its editor must be open)", label="Проект"),
     Arg("--out", help="output folder (default: paretogpu/out/frame_<project>_<time>, not in git)"),
     Arg("--suffix", help="appended to the snapshot folder name: frame_<project>_<time>_<suffix>",
         label="Суффикс снимка"),
     Arg("--timeout", type=int, default=1800, help="seconds", label="Таймаут снимка, с"),
     Arg("--max-events", type=int, default=0, help="stop after N events (0 = all)", label="Не больше событий")],
    run_command, phases=lambda v: ["rd_capture", "snapshot", "rd_counters"], title="Снимок кадра")
