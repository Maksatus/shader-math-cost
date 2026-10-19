import json
import os
from collections import Counter

from paretogpu.app import frame
from paretogpu.core import profile
from paretogpu.features.common import fail
from paretogpu.features.spec import Arg, Command
from paretogpu.model.errors import ParetoError
from paretogpu.model.variant import VariantKey
from paretogpu.store import workspace


def run_command(args):
    out = args.out or workspace.new_snapshot_dir(args.project, args.suffix)
    try:
        snap = frame.run(args.project, out, args.timeout, args.max_events)
    except ParetoError as e:
        fail(f"snapshot failed: {e}", e)
    with open(os.path.join(out, workspace.SNAPSHOT_FILE), "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
    print_snapshot(snap, out)
    return 0


def print_snapshot(snap, out):
    events = snap["events"]
    draws = [e for e in events if e["kind"] == "draw"]
    dev = snap.get("device")
    if dev:
        print(f"{dev['model']} ({dev.get('gpu') or 'GPU unknown'}, {snap['graphics_api']}, profile "
              f"{dev.get('profile') or 'of the device'}): {len(events)} calls in {snap.get('seconds') or 0:.0f} s")
    else:
        print(f"Unity {snap['unity']} ({snap['graphics_api']}, quality {snap['quality']}, "
              f"{'play' if snap['play_mode'] else 'edit'} mode): {len(events)} events in {snap['seconds']} s")
    print(profile.summary(profile.describe(snap, profile.frame_size(
        [{"rt_size": [e["rt"]["width"], e["rt"]["height"]], "pixels": e["pixel_count"], "stage": e["stage"]}
         for e in draws]))))
    stages = Counter(e["stage"] for e in events)
    print("stages: " + ", ".join(f"{s} {n}" for s, n in stages.most_common()))
    variants = {VariantKey.of(e) if e.get("code") else (e["shader"], e["pass"], tuple(e["keywords"])) for e in draws}
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
        print(f"renderdoc: {r['matched']} events matched, {r['missing']} without calls -> {r['capture']}"
              + (f", replayed on {r['replay']}" if r.get("replay") else ""))
        if r.get("mismatched"):
            print(f"  WARNING: {r['mismatched']} draws whose RenderDoc calls do not match the Frame Debugger event "
                  "(index or vertex count, or a full-screen pass with more pixels than its target; "
                  "ev['rd']['count_mismatch']): the call order went astray, their counters may be another draw's")
        used = {"ps_invocations", "vs_invocations"} | ({"cs_invocations"} if any(e["kind"] == "compute" for e in events)
                                                       else set())
        lost = used - set(r.get("counters") or [])
        if lost:
            print(f"  WARNING: the capture has no {', '.join(sorted(lost))}: vertices fall back to the Frame Debugger "
                  "count, compute threads to groups x group size, pixels to none (no fragment cost)")
    print(f"-> {os.path.join(out, workspace.SNAPSHOT_FILE)}")


FRAME = Command(
    "frame", "snapshot the frame of the open Unity editor: RenderDoc (loaded in the editor: Game tab -> Load RenderDoc) "
             "for the pixels and vertices, the Frame Debugger for the shader variant of every event",
    [Arg("--project", required=True, help="Unity project folder (its editor must be open)", label="Проект"),
     Arg("--out", label="Папка snapshot", help="output folder (default: paretogpu/out/frame_<project>_<time>, not in git)"),
     Arg("--suffix", help="appended to the snapshot folder name: frame_<project>_<time>_<suffix>",
         label="Суффикс snapshot"),
     Arg("--timeout", type=int, default=1800, help="seconds", label="Таймаут snapshot, с"),
     Arg("--max-events", type=int, default=0, help="stop after N events (0 = all)", label="Не больше events")],
    run_command, phases=lambda v: ["rd_capture", "snapshot", "rd_counters", "rd_draws"], title="Snapshot frame")
