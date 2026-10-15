import re

from paretogpu.model.frame import Event


def _blend(b):
    if not b:
        return None
    return f"{b.get('m_SrcBlend')} {b.get('m_DstBlend')}"


UI_MARKS = ("canvas.", "drawscreenspaceui", "gui.repaint", "uir.")
DEPTH_COPY = ("copy depth", "copydepth", "downsample depth")
POST_MARKS = ("postprocess", "uberpost", "finalpost", "bloom", "blit", "fullscreen", "copycolor", "final blit")


def _fullscreen(ev):
    return (ev.get("vertices") or 0) <= 6 and (ev.get("shader") or "").startswith("Hidden/")


def stage_of(ev):
    # a device draw has no LightMode, only its identified pass: URP names those passes after their LightMode
    path, lm = (ev.get("path") or "").lower(), (ev.get("light_mode") or ev.get("pass") or "").lower()
    rt, shader = ((ev.get("rt") or {}).get("name") or "").lower(), ev.get("shader") or ""
    if ev["kind"] == "compute":
        return "compute"
    if ev["kind"] != "draw":
        return "other"
    if any(m in path for m in UI_MARKS) or shader.startswith("UI/"):
        return "ui"
    if lm == "shadowcaster" or "shadow" in rt:
        return "shadow"
    if lm in ("depthonly", "depthnormals", "depthnormalsonly", "motionvectors") or "prepass" in path:
        return "prepass"
    if any(m in path for m in DEPTH_COPY) or "copydepth" in shader.lower():
        return "prepass"
    if "skybox" in path or shader.startswith("Skybox/"):
        return "opaque"
    if any(p in path for p in POST_MARKS) or _fullscreen(ev):
        return "post"
    if ev.get("blend") and ev["blend"] != "One Zero":
        return "transparent"
    if "transparent" in path:
        return "transparent"
    return "opaque"


def assign_stages(events):
    """A full-screen pass inside a native render pass with opaque geometry (MRT init, sky) is opaque, not post."""
    for ev in events:
        ev["stage"] = stage_of(ev)
    opaque_rp = {_native_pass(ev) for ev in events if ev["stage"] == "opaque" and not _fullscreen(ev)}
    for ev in events:
        if ev["stage"] == "post" and _fullscreen(ev) and _native_pass(ev) in opaque_rp - {None}:
            ev["stage"] = "opaque"
    return events


def _native_pass(ev):
    m = _RP_NUM.search(ev["path"] or "")
    return (ev["path"][:m.start()], m.group(1)) if m else None


def pixel_count(ev):
    """-> (PSInvocations of its RenderDoc calls or None, method); the Frame Debugger gives no pixel count."""
    if ev["kind"] != "draw":
        return 0, None
    if ev.get("rd") and ev["rd"].get("ps") is not None:
        return ev["rd"]["ps"], "renderdoc"
    return None, "none"


def normalize(raw) -> list[Event]:
    out = []
    for e in raw["events"]:
        x = e.get("data") or {}
        info = x.get("m_ShaderInfo") or {}
        kws = info.get("m_Keywords") or []
        # kind from the event type: Frame Debugger data keeps stale fields (a clear shows the previous draw's shader,
        # every event the last compute shader)
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
            # shader parameters: the bounds of dynamic loops
            "params": e.get("params") or {},
            "compute": {"shader": compute, "kernel": x.get("m_ComputeShaderKernelName"),
                        "groups": [x.get(f"m_ComputeShaderThreadGroups{a}") for a in "XYZ"],
                        "group_size": [x.get(f"m_ComputeShaderGroupSize{a}") for a in "XYZ"]} if compute else None,
        }
        if kind != "draw":
            for k in ("shader", "original_shader", "pass", "light_mode", "subshader", "pass_index", "blend", "mesh"):
                ev[k] = None
            ev["keywords"], ev["global_keywords"] = [], []
            ev["vertices"] = ev["indices"] = ev["instances"] = 0
        if kind == "other":
            ev["params"] = {}  # the data keeps the parameters of the last draw
        ev["pixel_count"], ev["pixel_method"] = pixel_count(ev)
        out.append(ev)
    return assign_stages(out)


_RP = re.compile(r"\(RP \d+:\d+\) ")
CMD = re.compile(r"^vkCmd\w+\(\d*\)$")  # a Vulkan command buffer executed inside another: not a marker
_RP_NUM = re.compile(r"\(RP (\d+):\d+\) ")
FULLSCREEN_SLACK = 1.01


def _norm(path):
    return "/".join(s for s in _RP.sub("", path or "").split("/") if s and not CMD.match(s))


def _ends(path, key):
    p =_norm(path)
    return p == key or (bool(key) and p.endswith("/" + key))


def _check(ev, sel, ps):
    """-> [what, Frame Debugger value, RenderDoc value] when the calls cannot be the event's, else None."""
    got = sum((a.get("indices") or 0) * max(1, a.get("instances") or 0) for a in sel)
    if ev.get("indices"):
        if got != ev["indices"]:
            return ["indices", ev["indices"], got]
    elif ev.get("vertices") and got != ev["vertices"]:
        return ["vertices", ev["vertices"], got]
    rt = ev.get("rt") or {}
    area = (rt.get("width") or 0) * (rt.get("height") or 0)
    if _fullscreen(ev) and area and ps is not None and ps > FULLSCREEN_SLACK * area:
        return ["pixels", area, ps]
    return None


def match(events: list[Event], actions, counters=None):
    """Sets ev["rd"] from the next m_DrawCallCount calls whose marker path ends with the event's (whole segments).
    The scan stops at a call of the next event's path, so a missing event never takes a later one's calls.
    A counter not in `counters` (rd_actions.json) is None, not 0: Mali has no pipeline statistics.
    -> (matched, missing, count-mismatched draws)."""
    calls = [a for a in actions if "draw" in a["kinds"] or "dispatch" in a["kinds"]]
    pos, matched, missing, mismatched = 0, 0, 0, 0
    wanted = [ev for ev in events if ev["kind"] in ("draw", "compute")]

    def total(sel, name):
        if counters is not None and name not in counters:
            return None
        return sum(a.get(name) or 0 for a in sel)
    for i, ev in enumerate(wanted):
        want = "dispatch" if ev["kind"] == "compute" else "draw"
        key, n = _norm(ev["path"]), max(1, ev["draw_calls"] or 1)
        nxt = wanted[i + 1] if i + 1 < len(wanted) else None
        nkey = _norm(nxt["path"]) if nxt else None
        nwant = ("dispatch" if nxt["kind"] == "compute" else "draw") if nxt else None
        got, j = [], pos
        while j < len(calls) and len(got) < n:
            a = calls[j]
            if want in a["kinds"] and _ends(a["path"], key):
                got.append(j)
            elif got:
                break  # the calls of one event are consecutive
            elif nxt and nkey != key and nwant in a["kinds"] and _ends(a["path"], nkey):
                break
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
        bad = _check(ev, sel, ps) if ev["kind"] == "draw" else None
        if bad:
            ev["rd"]["mismatch"], ev["rd"]["count_mismatch"] = bad[0], bad[1:]
            mismatched += 1
        matched += 1
    return matched, missing, mismatched
