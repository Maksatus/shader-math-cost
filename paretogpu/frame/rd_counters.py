# Runs inside RenderDoc's UI python (qrenderdoc --python rd_counters.py), not in the normal interpreter.
# Opens the capture in PARETOGPU_RDC, writes PARETOGPU_OUT: every action (draw, dispatch, clear, ...) with its
# marker path, index / instance counts and the GPU counters PSInvocations, VSInvocations, CSInvocations,
# SamplesPassed (D3D11 pipeline statistics: how many times the pixel / vertex / compute shader ran for that call).
import json
import os
import traceback

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]


def ok(r):
    code = getattr(r, "code", r)
    return code == rd.ResultCode.Succeeded


def main():
    cap = rd.OpenCaptureFile()
    r = cap.OpenFile(os.environ["PARETOGPU_RDC"], "", None)
    if not ok(r):
        raise RuntimeError(f"cannot open capture: {r}")
    if not cap.LocalReplaySupport():
        raise RuntimeError("capture cannot be replayed locally")
    r, controller = cap.OpenCapture(rd.ReplayOptions(), None)
    if not ok(r):
        raise RuntimeError(f"cannot replay capture: {r}")
    sf = controller.GetStructuredFile()
    wanted = {rd.GPUCounter.PSInvocations: "ps_invocations", rd.GPUCounter.VSInvocations: "vs_invocations",
              rd.GPUCounter.CSInvocations: "cs_invocations", rd.GPUCounter.SamplesPassed: "samples_passed",
              rd.GPUCounter.RasterizedPrimitives: "rasterized_primitives", rd.GPUCounter.EventGPUDuration: "gpu_duration"}
    avail = set(controller.EnumerateCounters())
    use = [c for c in wanted if c in avail]
    counters = {}
    for res in controller.FetchCounters(use):
        desc = controller.DescribeCounter(res.counter)
        v = res.value.d if desc.resultType == rd.CompType.Float else res.value.u64
        counters.setdefault(res.eventId, {})[wanted[res.counter]] = v

    actions = []

    def walk(items, path):
        for a in items:
            name = a.GetName(sf)
            flags = a.flags
            kinds = [k for k, f in (("draw", rd.ActionFlags.Drawcall), ("dispatch", rd.ActionFlags.Dispatch),
                                    ("clear", rd.ActionFlags.Clear), ("copy", rd.ActionFlags.Copy),
                                    ("resolve", rd.ActionFlags.Resolve), ("present", rd.ActionFlags.Present),
                                    ("marker", rd.ActionFlags.PushMarker)) if flags & f]
            if a.children:
                walk(a.children, path + [name])
            else:
                actions.append({"event": a.eventId, "name": name, "path": "/".join(path), "kinds": kinds,
                                "indices": a.numIndices, "instances": a.numInstances,
                                **counters.get(a.eventId, {})})

    walk(controller.GetRootActions(), [])
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"counters": [wanted[c] for c in use], "actions": actions}, f, indent=1)
    controller.Shutdown()
    cap.Shutdown()


try:
    main()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
os._exit(0)
