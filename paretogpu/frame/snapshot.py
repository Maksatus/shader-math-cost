"""Frame snapshot through the Frame Debugger (plan item K1.1).

Runs unity/ParetoGpuFrame.cs in the open editor (Unity CLI run_script), waits for
<out>/frame.json (raw Frame Debugger data of every event) and turns it into
<out>/frame_events.json: one record per event with the shader variant, vertices,
render target and the frame stage (shadow / prepass / opaque / transparent / post /
compute / other).

The frame is whatever the Game view shows: its resolution and the current quality level.
"""
import json
import os
import re
import shutil
import time

from paretogpu import progress as progress_ui
from paretogpu.frame import renderdoc as rdoc
from paretogpu.unity import export as unity

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "unity", "ParetoGpuFrame.cs")


class SnapshotError(RuntimeError):
    pass


def capture(project, out, timeout=1800, max_events=0, progress=print):
    """Snapshot the frame of the open editor into <out>/frame.json; returns the parsed raw data."""
    project, out = os.path.abspath(project), os.path.abspath(out)
    progress_ui.phase("snapshot")
    if not unity.editor_ready(project, allow_play=True):
        raise SnapshotError(f"the editor of {project} does not answer Unity CLI or is busy (compiling, importing): "
                            "the snapshot needs the open editor with the frame in the Game view")
    os.makedirs(out, exist_ok=True)
    config = os.path.join(out, "frame_config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"out": out, "max_events": max_events}, f)
    d = unity._cli_json(["command", "run_script", "--project-path", project, "--timeout_ms", "60000",
                         "--file", os.path.abspath(SCRIPT), "--entry", "ParetoGpuFrame.Start",
                         "--args", json.dumps([config])], 120)
    res = (d.get("data") or {}).get("result") or {}
    if res.get("result") != "started":
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise SnapshotError(f"could not start: {res.get('result') or diag or unity._errors(d)}")
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
            progress_ui.step(n, n, note="done")  # Unity reports every 25 events: the last count is never shown
            return raw
        if os.path.exists(paths["progress"]):
            with open(paths["progress"], encoding="utf-8", errors="replace") as f:
                p = f.read().strip()
            if p != last:
                if progress:
                    progress(f"  {p}")
                m = re.fullmatch(r"events (\d+)/(\d+)", p)
                if m:
                    progress_ui.step(int(m.group(1)), int(m.group(2)))
                else:
                    progress_ui.step(None, note=p)
            last = p
        time.sleep(0.5)
    raise SnapshotError(f"no result after {timeout} s (progress: {last})")


def _blend(b):
    if not b:
        return None
    return f"{b.get('m_SrcBlend')} {b.get('m_DstBlend')}"


def stage_of(ev):
    """Frame stage of a normalized event, from the light mode, render target, blend state and marker path."""
    path, lm, rt = ev["path"].lower(), (ev["light_mode"] or "").lower(), (ev["rt"]["name"] or "").lower()
    if ev["kind"] == "compute":
        return "compute"
    if ev["kind"] != "draw":
        return "other"
    if "canvas." in path or (ev["shader"] or "").startswith("UI/"):
        return "ui"
    if lm == "shadowcaster" or "shadow" in rt:
        return "shadow"
    if lm in ("depthonly", "depthnormals", "depthnormalsonly", "motionvectors") or "prepass" in path:
        return "prepass"
    post = ("postprocess", "uberpost", "finalpost", "bloom", "blit", "fullscreen", "copycolor", "final blit")
    if any(p in path for p in post) or (ev["vertices"] <= 6 and (ev["shader"] or "").startswith("Hidden/")):
        return "post"
    if ev["blend"] and ev["blend"] != "One Zero":
        return "transparent"
    if "transparent" in path:
        return "transparent"
    return "opaque"


def pixel_count(ev):
    """(fragments the event shaded, how it was found): PSInvocations of its RenderDoc calls (K1.3), how many times
    the pixel shader really ran. The Frame Debugger replay counts no pixels (its RT diff is gone: it was an
    underestimate and the slow part of the snapshot)."""
    if ev["kind"] != "draw":
        return 0, None
    if ev.get("rd") and ev["rd"].get("ps") is not None:
        return ev["rd"]["ps"], "renderdoc"
    return None, "none"


def normalize(raw):
    """frame.json (raw Frame Debugger data) -> list of event records."""
    out = []
    for e in raw["events"]:
        x = e.get("data") or {}
        info = x.get("m_ShaderInfo") or {}
        kws = info.get("m_Keywords") or []
        # the event type decides what the event is: the data keeps stale fields of earlier events
        # (a clear shows the shader of the previous draw, every event the last compute shader)
        etype = e.get("type") or ""
        if "Dispatch" in etype:
            kind = "compute"
        elif etype.startswith(("Clear", "Resolve", "SetRenderTarget", "BeginSubpass", "NextSubpass", "EndSubpass")):
            kind = "other"
        else:
            kind = "draw" if x.get("m_RealShaderName") and (x.get("m_VertexCount") or x.get("m_IndexCount")) else "other"
        compute = (x.get("m_ComputeShaderName") or "") if kind == "compute" else ""
        ev = {
            "index": e["index"], "type": etype, "path": e.get("name") or "",
            "kind": kind,
            "object": (e.get("object") or {}).get("name") if isinstance(e.get("object"), dict) else None,
            "mesh": (x.get("m_Mesh") or {}).get("name") if isinstance(x.get("m_Mesh"), dict) else None,
            "meshes": e.get("meshes") or [],
            "pixels": e.get("pixels"),
            "shader": x.get("m_RealShaderName") or None, "original_shader": x.get("m_OriginalShaderName") or None,
            "pass": x.get("m_PassName") or None, "light_mode": x.get("m_PassLightMode") or None,
            "subshader": x.get("m_SubShaderIndex"), "pass_index": x.get("m_ShaderPassIndex"),
            "keywords": sorted(k["m_Name"] for k in kws if k.get("m_Name")),
            "global_keywords": sorted(k["m_Name"] for k in kws if k.get("m_IsGlobal")),
            "vertices": x.get("m_VertexCount") or 0, "indices": x.get("m_IndexCount") or 0,
            "instances": x.get("m_InstanceCount") or 0, "draw_calls": x.get("m_DrawCallCount") or 0,
            "rt": {"name": x.get("m_RenderTargetName"), "width": x.get("m_RenderTargetWidth"),
                   "height": x.get("m_RenderTargetHeight"), "count": x.get("m_RenderTargetCount"),
                   "backbuffer": x.get("m_RenderTargetIsBackBuffer")},
            "blend": _blend(x.get("m_BlendState")),
            "write_mask": (x.get("m_BlendState") or {}).get("m_WriteMask"),
            "ztest": (x.get("m_DepthState") or {}).get("m_DepthFunc"),
            "zwrite": bool((x.get("m_DepthState") or {}).get("m_DepthWrite")),
            "cull": (x.get("m_RasterState") or {}).get("m_CullMode"),
            "batch_break": x.get("m_BatchBreakCause"),
            "compute": {"shader": compute, "kernel": x.get("m_ComputeShaderKernelName"),
                        "groups": [x.get(f"m_ComputeShaderThreadGroups{a}") for a in "XYZ"],
                        "group_size": [x.get(f"m_ComputeShaderGroupSize{a}") for a in "XYZ"]} if compute else None,
        }
        if kind != "draw":
            for k in ("shader", "original_shader", "pass", "light_mode", "subshader", "pass_index", "blend", "mesh"):
                ev[k] = None
            ev["keywords"], ev["global_keywords"] = [], []
            ev["vertices"] = ev["indices"] = ev["instances"] = 0
        ev["stage"] = stage_of(ev)
        ev["pixel_count"], ev["pixel_method"] = pixel_count(ev)
        out.append(ev)
    return out


RD_MIN_MATCHED = 0.5  # share of the draws and dispatches a RenderDoc capture must hold to be the game frame
# A capture of the Game view that holds only the editor UI (the repaint did not render the cameras: see
# ParetoGpuRenderDoc.cs, state 1) is a few MB, the game frame hundreds: a small one is taken again at once, a big
# one is checked by matching its calls. Since the capture waits for renderViewCallNeededInOnGUI this is a safety net.
RD_ATTEMPTS = 5
RD_UI_ONLY_BYTES = 20e6


def renderdoc_pixels(project, out, rd, events, progress=print):
    """Counters of the RenderDoc capture -> ev["rd"] and the pixels of every event. A capture without the game
    frame is taken again (Play Mode is still paused: the same frame); if none of RD_ATTEMPTS is the frame,
    SnapshotError: the snapshot was asked to be exact and must not fall back to diff pixels silently."""
    want = sum(1 for e in events if e["kind"] in ("draw", "compute"))
    rdc = os.path.join(out, "frame.rdc")
    for attempt in range(1, RD_ATTEMPTS + 1):
        size = os.path.getsize(rd["capture"])
        if size < RD_UI_ONLY_BYTES and want:
            why = f"the RenderDoc capture is not the game frame ({size / 1e6:.1f} MB: only the editor UI)"
        else:
            progress_ui.phase("rd_counters")
            progress("  renderdoc counters")
            if os.path.exists(rdc):
                os.remove(rdc)
            try:
                shutil.move(rd["capture"], rdc)  # keep the capture next to the snapshot (not in git)
                path = rdc
            except OSError:
                path = rd["capture"]
            acts = rdoc.counters(path, os.path.join(out, "rd_actions.json"))
            matched, missing, mismatched = rdoc.match(events, acts["actions"], acts.get("counters"))
            if not want or matched >= RD_MIN_MATCHED * want:
                break
            calls = sum(1 for a in acts["actions"] if "draw" in a["kinds"] or "dispatch" in a["kinds"])
            why = (f"the RenderDoc capture is not the game frame: {matched} of {want} events found in it "
                   f"({calls} calls, mostly the editor UI)")
        if os.path.exists(rd["capture"]) and os.path.abspath(rd["capture"]) != rdc:
            os.remove(rd["capture"])
        if attempt == RD_ATTEMPTS:
            raise SnapshotError(f"{why}, {RD_ATTEMPTS} attempts. Make the Game view visible (not hidden behind "
                                "another tab or a minimized window) and take the snapshot again")
        progress(f"  {why}: capturing again ({attempt + 1}/{RD_ATTEMPTS})")
        progress_ui.phase("rd_capture")
        rd = rdoc.capture(project, out)
    for ev in events:
        ev["pixel_count"], ev["pixel_method"] = pixel_count(ev)
    return {"capture": path, "matched": matched, "missing": missing, "mismatched": mismatched,
            "counters": acts["counters"], "attempts": attempt}


def run(project, out, timeout=1800, max_events=0, progress=print, renderdoc=True):
    """Snapshot the frame: capture it with RenderDoc, read its events with the Frame Debugger (Play Mode is paused
    for both and set back after) and take the pixels of every event from its PSInvocations. renderdoc=False only
    for tests: the events then have no pixels."""
    out = os.path.abspath(out)
    rd = None
    if renderdoc:
        progress_ui.phase("rd_capture")
        progress("  renderdoc capture")
        rd = rdoc.capture(project, out)
    was_paused = (rd or {}).get("was_paused", False)
    try:
        raw = capture(project, out, timeout, max_events, progress)
        events = normalize(raw)
        meta = {k: v for k, v in raw.items() if k != "events"}
        if rd:
            # Play Mode stays paused until the capture is checked: a capture again is of the same frame
            meta["renderdoc"] = renderdoc_pixels(project, out, rd, events, progress)
    finally:
        if rd:
            rdoc.resume(project, was_paused)
    with open(os.path.join(out, "frame_events.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({**meta, "events": events}, f, indent=1, ensure_ascii=False)
    return meta, events
