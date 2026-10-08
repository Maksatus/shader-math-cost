"""Frame snapshot data (plan items K1.1, K1.3): Frame Debugger events -> event records, RenderDoc calls -> counters.

normalize(raw)  frame.json (raw Frame Debugger data) -> one record per event with the shader variant, vertices,
                render target and the frame stage (shadow / prepass / opaque / transparent / post / compute / other);
match(events, actions)  the RenderDoc calls of every event (ev["rd"]): the next calls with the same marker path.
"""
import re

from paretogpu.model.frame import Event


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


def normalize(raw) -> list[Event]:
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


_RP = re.compile(r"\(RP \d+:\d+\) ")


def _norm(path):
    return _RP.sub("", path or "").strip("/")


def match(events: list[Event], actions, counters=None):
    """Attach to every draw / compute event its RenderDoc calls:
    ev["rd"] = {calls, events, ps / ps_invocations, vs / vs_invocations, cs_invocations, samples, gpu_ms}.
    The calls of an event are the next calls (draws for draws, dispatches for compute) whose marker path ends with
    the event's path; a Frame Debugger event holds m_DrawCallCount calls (several for an SRP batch).
    counters: the counters the capture has (rd_actions.json "counters"); a counter it does not have is None, not 0
    (Mali has no pipeline statistics). A draw whose calls do not add up to the Frame Debugger index count
    (sum of indices x instances) gets "count_mismatch": [Frame Debugger, RenderDoc] — the order went astray.
    Returns (matched events, events without calls, matched draws with a count mismatch)."""
    calls = [a for a in actions if "draw" in a["kinds"] or "dispatch" in a["kinds"]]
    pos, matched, missing, mismatched = 0, 0, 0, 0

    def total(sel, name):
        if counters is not None and name not in counters:
            return None
        return sum(a.get(name) or 0 for a in sel)
    for ev in events:
        if ev["kind"] not in ("draw", "compute"):
            continue
        want = "dispatch" if ev["kind"] == "compute" else "draw"
        key, n = _norm(ev["path"]), max(1, ev["draw_calls"] or 1)
        got, j = [], pos
        while j < len(calls) and len(got) < n:
            a = calls[j]
            if want in a["kinds"] and (_norm(a["path"]).endswith(key) or (not key and not a["path"])):
                got.append(j)
            elif got:
                break  # the calls of one event are consecutive
            j += 1
        if len(got) != n:
            ev["rd"] = None
            missing += 1
            continue
        pos = got[-1] + 1
        sel = [calls[k] for k in got]
        ps, vs, cs = total(sel, "ps_invocations"), total(sel, "vs_invocations"), total(sel, "cs_invocations")
        gpu = total(sel, "gpu_duration")
        ev["rd"] = {"calls": n, "events": [a["event"] for a in sel], "ps": ps, "vs": vs,
                    "ps_invocations": ps, "vs_invocations": vs, "cs_invocations": cs,
                    "samples": total(sel, "samples_passed"),
                    "gpu_ms": None if gpu is None else round(gpu * 1000, 4)}
        if ev["kind"] == "draw" and ev.get("indices"):
            got_indices = sum((a.get("indices") or 0) * max(1, a.get("instances") or 0) for a in sel)
            if got_indices != ev["indices"]:
                ev["rd"]["count_mismatch"] = [ev["indices"], got_indices]
                mismatched += 1
        matched += 1
    return matched, missing, mismatched
