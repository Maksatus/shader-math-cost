# Runs inside qrenderdoc --python (RenderDoc's UI python, not the normal interpreter).
# In: capture PARETOGPU_RDC, PARETOGPU_LIB. Out: PARETOGPU_OUT {"counters", "actions": leaf actions with marker path,
# counters (*_invocations = times that shader stage ran), a call's uniform "blocks" {"@set.binding": base64} and the
# "textures" it reads [{"image", "stage", "filter"}] (rd_vulkan)};
# a traceback goes to <out>.error.
import json
import os
import sys
import traceback

import renderdoc as rd

sys.path.insert(0, os.environ["PARETOGPU_LIB"])
import rd_replay  # noqa: E402

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
              rd.GPUCounter.CSInvocations: "cs_invocations", rd.GPUCounter.SamplesPassed: "samples_passed"}
    avail = set(controller.EnumerateCounters())
    use = [c for c in wanted if c in avail]
    counters = {}
    for res in controller.FetchCounters(use):
        desc = controller.DescribeCounter(res.counter)
        v = res.value.d if desc.resultType == rd.CompType.Float else res.value.u64
        counters.setdefault(res.eventId, {})[wanted[res.counter]] = v

    actions, calls = [], rd_replay.state(controller, os.environ["PARETOGPU_RDC"]).calls

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
                                **counters.get(a.eventId, {}),
                                **({"blocks": calls[a.eventId]["blocks"], "textures": calls[a.eventId]["textures"]}
                                   if {"draw", "dispatch"} & set(kinds) else {}),
                                **({"unique_vertices": calls[a.eventId]["unique_vertices"]} if "draw" in kinds else {})})

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
