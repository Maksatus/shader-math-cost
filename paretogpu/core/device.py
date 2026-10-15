"""Android frame (RenderDoc for Arm, adapters/renderdoc/rd_device.py) as a snapshot.

Snapshot: one event per draw / dispatch with the phone's SPIR-V (ev["code"], identified as a project variant later by
app/variants.identify); global keywords None: the build's are not in the capture. pixels = Samples Passed / target
samples (samples_shaded with discard removed when present; 0 without a fragment shader; editor: PSInvocations);
vertices = distinct indices x instances (editor: VSInvocations); params = uniform block bytes {"@set.binding": base64}
for core/loops.spirv_bounds. Paths get "(RP n:m)" like the editor's native passes.
"""
import hashlib
import re

from paretogpu.core.events import CMD, assign_stages

SKIP = re.compile(r"^(vkCmd|ID3D|RenderLoop\.|Shadows\.|Camera\.|GUI\.|UI\.|Setup |PlayerEndOfFrame|FrameTime\.|"
                  r"Inl_|Clear Rendering State|ScriptableRenderer)")
_RP = re.compile(r"^\(RP \d+:\d+\) ")
SHARED = " (shared shader)"  # Unity's suffix for a module several shaders share
PIXELS, VERTICES = "samples", "indices"  # pixel_method / vertex_method values
SHADERS = "device/shaders"  # snapshot-relative folder of the phone's SPIR-V
CAPTURE = "frame.rdc"
IMGUI = "GUI.Repaint"
KIND_OF = (("draw", "draw"), ("dispatch", "compute"))


def event_path(path, rp):
    """Editor-style path: nested command buffers dropped, "(RP n:m)" on the first marker under ExecuteRenderGraph."""
    segs = [s for s in (path or "").split("/") if s and not CMD.match(s)]
    if rp is None or not segs:
        return "/".join(segs)
    i = segs.index("ExecuteRenderGraph") + 1 if "ExecuteRenderGraph" in segs[:-1] else len(segs) - 1
    segs[i] = f"(RP {rp[0]}:{rp[1]}) {segs[i]}"
    return "/".join(segs)


def _shader_name(sh, pipeline):
    """A shared module is named after its first shader; a vertex module keeps SHARED because a depth-only draw may be
    another shader's (app/variants.identify resolves it). None: RenderDoc named the objects itself."""
    for s in ("frag", "comp", "vert"):
        name = (sh.get(s) or {}).get("name")
        if name:
            return name[:-len(SHARED)] if name.endswith(SHARED) and s != "vert" else name
    return pipeline


def _event(index, a, d, path):
    """a: action of the answer, d: its draw record; -> model/frame.Event."""
    sh = d.get("shaders") or {}
    kind = dict(KIND_OF)["dispatch" if "dispatch" in a["kinds"] else "draw"]
    code = {s: f"{SHADERS}/{x['file']}" for s, x in sh.items()}
    code["hash"] = hashlib.sha1("|".join(f"{s}:{sh[s]['hash']}" for s in sorted(sh)).encode()).hexdigest()[:12]
    name = _shader_name(sh, d.get("pipeline"))
    ev = {"index": index, "type": a["name"].split("(")[0], "path": path, "kind": kind,
          "object": (d.get("meshes") or [None])[0], "mesh": (d.get("meshes") or [None])[0],
          "meshes": d.get("meshes") or [], "pixels": None, "shader": name if kind == "draw" else None,
          "original_shader": name if kind == "draw" else None, "pass": None, "light_mode": None,
          "subshader": 0 if kind == "draw" else None, "pass_index": 0 if kind == "draw" else None,
          "keywords": [], "global_keywords": None, "vertices": 0, "indices": 0, "instances": 0, "draw_calls": 1,
          "rt": {k: (d.get("rt") or {}).get(k) for k in ("name", "width", "height", "count", "backbuffer")},
          "blend": d.get("blend"), "write_mask": d.get("write_mask"), "ztest": d.get("ztest"),
          "zwrite": d.get("zwrite"), "cull": d.get("cull"), "batch_break": None, "params": d.get("blocks") or {},
          "compute": None,
          "code": code if sh else None, "pixel_count": 0, "pixel_method": None}
    rd = {"calls": 1, "events": [a["event"]], "ps": None, "vs": None, "ps_invocations": None, "vs_invocations": None,
          "cs_invocations": None, "samples": None, "gpu_ms": None}
    if kind == "compute":
        groups, size = d.get("groups") or [], d.get("group_size") or []
        ev["compute"] = {"shader": name, "kernel": (sh.get("comp") or {}).get("entry"), "groups": groups,
                         "group_size": size}
        ev["rt"] = {"name": None, "width": None, "height": None, "count": 0, "backbuffer": False}
    else:
        inst = max(1, a.get("instances") or 1)
        verts = d.get("unique_vertices") if d.get("unique_vertices") is not None else a.get("indices") or 0
        sp = d.get("samples_passed")
        shaded = d.get("samples_shaded") if d.get("samples_shaded") is not None else sp
        px = (0 if "frag" not in sh else None if shaded is None
              else round(shaded / max(1, (d.get("rt") or {}).get("samples") or 1)))
        ev.update(vertices=verts * inst, indices=(a.get("indices") or 0) * inst if d.get("indexed") else 0,
                  instances=a.get("instances") or 0, pixel_count=px if px is not None else 0,
                  pixel_method=PIXELS if px is not None else "none")
        rd.update(ps=px, vs=verts * inst, ps_invocations=px, vs_invocations=verts * inst, samples=sp,
                  discards=bool(d.get("discards")), samples_shaded=d.get("samples_shaded"),
                  pixel_method=PIXELS, vertex_method=VERTICES if d.get("unique_vertices") is not None else "calls")
    ev["rd"] = rd
    return ev


def render_passes(actions):
    """-> {event: [render pass, subpass] or None}. By command names, not RenderDoc pass flags (those also mark nested
    command buffers and subpasses)."""
    out, rp, sub, inside = {}, -1, 0, False
    for a in sorted(actions, key=lambda a: a["event"]):
        name = a["name"]
        if name.startswith("vkCmdBeginRenderPass"):
            rp, sub, inside = rp + 1, 0, True
        elif name.startswith("vkCmdNextSubpass") and inside:
            sub += 1
        out[a["event"]] = [rp, sub] if inside else None
        if name.startswith("vkCmdEndRenderPass"):
            inside = False
    return out


def snapshot(answer, meta):
    """-> (frame_events.json, rd_actions.json, rd_memory.json). Load-op clears become "clear" actions (core/memory: not
    loaded); IMGUI draws (development build label) are dropped since the editor frame has none."""
    actions, draws = answer.get("actions") or [], {int(k): v for k, v in (answer.get("draws") or {}).items()}
    rps = render_passes(actions)
    clears = {int(e) for us in (answer.get("usage") or {}).values() for e, u in us if u == "Clear"}
    events, rows, imgui = [], [], 0
    for a in sorted(actions, key=lambda a: a["event"]):
        path = event_path(a["path"], rps.get(a["event"]))
        d = draws.get(a["event"])
        if d is not None and "draw" in a["kinds"] and IMGUI in path.split("/"):
            imgui += 1
            d = None
        if d is not None and {"draw", "dispatch"} & set(a["kinds"]):
            events.append(_event(len(events), a, d, path))
            rd = events[-1]["rd"]
            counts = {"ps_invocations": rd["ps_invocations"], "vs_invocations": rd["vs_invocations"],
                      "cs_invocations": rd["cs_invocations"], "samples_passed": rd["samples"]}
        else:
            counts = {"ps_invocations": 0, "vs_invocations": 0, "cs_invocations": 0, "samples_passed": 0}
        kinds = [k for k in a["kinds"] if k not in ("begin_pass", "end_pass")]
        if a["event"] in clears and "clear" not in kinds:
            kinds.append("clear")
        rows.append({"event": a["event"], "name": a["name"], "path": path, "kinds": kinds,
                     "indices": a.get("indices") or 0, "instances": a.get("instances") or 0, **counts})
    assign_stages(events)
    draw_events = [e for e in events if e["kind"] == "draw"]
    snap = {
        "project": meta.get("project"), "unity": None,
        "graphics_api": answer["api"], "quality": None, "quality_level": None,
        "build_target": "Android", "screen": answer["screen"], "rotation": answer["rotation"], "pipeline": None,
        "build_pipeline": meta.get("build_pipeline"),
        "play_mode": True,
        "seconds": meta.get("seconds"), "total_events": len(events),
        "device": {"model": answer.get("device"), "serial": answer.get("serial"), "gpu": meta.get("gpu"),
                   "app": meta.get("app"), "captured_at": meta.get("captured_at"),
                   "discard_error": answer.get("discard_error"), "imgui_draws": imgui,
                   "discard_unshaded": sum(1 for e in events if e["rd"].get("discards")
                                           and e["rd"].get("samples_shaded") is None)},
        "renderdoc": {"capture": CAPTURE, "replay": answer["serial"], "matched": len(events), "missing": 0,
                      "mismatched": 0,
                      "counters": ["ps_invocations", "vs_invocations", "samples_passed"] if draw_events else [],
                      "attempts": 1},
        "events": events,
    }
    return (snap, {"counters": ["ps_invocations", "vs_invocations", "cs_invocations", "samples_passed"],
                   "actions": rows},
            {"textures": answer.get("textures") or [], "usage": answer.get("usage") or {}})


def markers(path):
    segs = (path or "").split("/")
    if "ExecuteRenderGraph" in segs:
        segs = segs[segs.index("ExecuteRenderGraph") + 1:]
    return [m for m in (_RP.sub("", s) for s in segs) if m and not SKIP.match(m)]
