# Runs inside RenderDoc for Arm GPUs' qrenderdoc --python. Shader cost on a device whose Mali counters RenderDoc cannot
# replay (client: the driver crashes or hangs): the frame replayed whole, one replay at a time with idle gaps, first as
# captured, then with each shader swapped for its stub (rd_vulkan.KNOCKOUTS) in turn; an outside profiler (Streamline's
# gatord, adapters/streamline.py) records the GPU meanwhile and core/sweep.py matches its bursts to these replays.
# In: PARETOGPU_IN {"serial", "capture", "repeats", "go": file whose appearance starts the replays}, PARETOGPU_LIB.
# Out: PARETOGPU_OUT {"jobs": [{"stage", "mode", "file", "events", "error"}], "marks": [[job, wall time]]} (job -1:
# the frame as captured, -2: the warm-up replay); progress "ready <n shaders>", then "shader cost: k/n".
import json
import os
import sys
import time
import traceback

import renderdoc as rd

sys.path.insert(0, os.environ["PARETOGPU_LIB"])
import rd_replay  # noqa: E402
import rd_vulkan  # noqa: E402

out_path = os.environ["PARETOGPU_OUT"]
GAP = 0.4    # idle seconds after each replay: the profiler sees one GPU burst per replay
PAUSE = 1.2  # idle seconds between groups (a swap replays the frame once more by itself)
STAGE = {"frag": rd.ShaderStage.Pixel, "vert": rd.ShaderStage.Vertex}


def step(text):
    with open(out_path + ".progress", "w", encoding="utf-8") as f:
        f.write(text)


def main():
    with open(os.environ["PARETOGPU_IN"], encoding="utf-8") as f:
        job = json.load(f)
    rp = rd_replay.Replay(job["capture"], job["serial"], step=step)
    f = rd_replay.state(rp.c, job["capture"])
    last = max(f.calls)
    ids = {int(x.resourceId): x.resourceId for x in rp.c.GetResources()}
    jobs, marks = [], []
    todo = rd_vulkan.knockouts(f)
    for stage, mode, make, module, (entry, raw, events) in todo:
        jobs.append({"stage": stage, "mode": mode, "file": rd_vulkan.shader_file(raw, entry, stage)[1],
                     "events": events, "error": None})

    def group(k):
        if rp.moved + job["repeats"] + 4 >= rd_replay.REOPEN:
            rp.moved = rd_replay.REOPEN
            rp.go(last, True)  # reopens the capture (swaps set again) now, not inside the group
            time.sleep(PAUSE)
        for _ in range(job["repeats"]):
            marks.append([k, time.time()])
            rp.go(last, True)
            rp.keepalive()
            time.sleep(GAP)
        time.sleep(PAUSE)

    step(f"ready {len(todo)}")
    while not os.path.exists(job["go"]):
        rp.keepalive()
        time.sleep(0.2)
    marks.append([-2, time.time()])
    rp.go(last, True)  # warms caches and clocks: marked (the time base) but not measured
    time.sleep(PAUSE)
    group(-1)
    for k, (stage, mode, make, module, (entry, raw, events)) in enumerate(todo):
        step(f"shader cost: {k + 1}/{len(todo)}")
        stub = make(raw)
        if stub is None:
            jobs[k]["error"] = "no_output"
            continue
        failed = rp.swap({ids[module]: (entry, stub, STAGE[stage])})
        if failed:
            jobs[k]["error"] = str(next(iter(failed.values())))[:300]
            continue
        time.sleep(PAUSE)  # the swap's own replay
        group(k)
        rp.unswap()
        time.sleep(PAUSE)
    group(-1)
    rp.close()
    with open(out_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"jobs": jobs, "marks": marks}, f)
    os.replace(out_path + ".tmp", out_path)


try:
    main()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as fh:
        fh.write(traceback.format_exc())
os._exit(0)
