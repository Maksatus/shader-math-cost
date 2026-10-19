"""Android frame (RenderDoc for Arm, adapters/renderdoc/rd_device.py) as a snapshot.

Snapshot: one event per draw / dispatch with the phone's SPIR-V (ev["code"], identified as a project variant later by
app/variants.identify); global keywords None: the build's are not in the capture. pixels = Samples Passed / target
samples (samples_shaded with discard removed when present; 0 without a fragment shader; editor: PSInvocations);
vertices = distinct indices x instances (editor: VSInvocations); params = uniform block bytes {"@set.binding": base64}
for core/loops.spirv_bounds. Paths get "(RP n:m)" like the editor's native passes.
"""
import bisect
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
                   "discard_error": answer.get("discard_error"), "imgui_draws": imgui, "mali": answer.get("mali"),
                   "knockout": answer.get("knockout"),
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


EXEC = "Execution core active cycles"
PIPES = {"A": "Arithmetic unit issue cycles", "LS": "Load/store unit issue cycles", "T": "Texture unit issue cycles",
         "V": "Varying unit issue cycles"}


def hardware(dev, actions, events, rows, core):
    """The phone's Mali counters per render pass (dev["mali"], rd_device.py) beside the model's cycles of the same pass
    on the phone's core: draws by their first call, Mali tile rows by their pass. No correction either way.
    -> {"core", "names", "passes": [{"event", "name", "draws", "model", "tile", "hw"}], "model", "exec",
    "exec_geometry"} or None; exec leaves out the geometry-only passes, exec_geometry is theirs."""
    mali = (dev or {}).get("mali")
    if not mali:
        return None
    begins = sorted(a["event"] for a in actions if a["name"].startswith("vkCmdBeginRenderPass"))
    first = {ev["index"]: (ev.get("rd") or {}).get("events", [None])[0] for ev in events}
    if mali.get("error") or not mali.get("passes"):
        return {"core": core, "error": mali.get("error") or "empty", "names": [], "passes": [],
                "shader_cost": shader_cost(dev.get("knockout"), events, begins, [], rows, first, core)}
    path = {a["event"]: a.get("path") or "" for a in actions}
    out = {b: {"event": b, "name": None, "draws": 0, "model": 0.0, "tile": 0.0} for b in begins}
    for r in rows:
        x = (r.get("cost") or {}).get(core)
        call = r.get("tile_event") if r["kind"] == "tile" else first.get(r["index"])
        if not x or call is None or not begins or call < begins[0]:
            continue
        p = out[begins[bisect.bisect_right(begins, call) - 1]]
        if r.get("geometry"):
            p["geometry"] = True
        else:
            p["model"] += x["total"]
        if r["kind"] == "tile":
            p["tile"] += x["total"]
        else:
            p["draws"] += 1
            sh = p.setdefault("shaders", {})
            k = r.get("shader") or r.get("object") or "—"
            sh[k] = sh.get(k, 0) + 1
            what = (markers(r.get("path")) or [r["stage"]])[-1] if r["stage"] in ("post", "other") else r["stage"]
            p.setdefault("what", {})
            p["what"][what] = p["what"].get(what, 0.0) + x["total"]
    names = mali["names"]
    res = []
    for b in begins:
        hw = (mali["passes"].get(str(b)) or mali["passes"].get(b))
        p = out[b]
        if hw is None and not p["model"]:
            continue
        what = p.pop("what", {})
        p["name"] = " + ".join(sorted(what, key=lambda k: -what[k])) or (markers(path.get(b)) or ["—"])[-1]
        p["hw"] = dict(zip(names, hw)) if hw else None
        res.append(p)
    geo = [p.get("geometry") and not p["model"] for p in res]  # shadow/prepass passes: the model prices none of them
    ex = [(p["hw"] or {}).get(EXEC) or 0 for p in res]
    return {"core": core, "error": None, "names": names, "passes": res, "model": sum(p["model"] for p in res),
            "exec": sum(e for e, g in zip(ex, geo) if not g), "exec_geometry": sum(e for e, g in zip(ex, geo) if g),
            "shader_cost": shader_cost(dev.get("knockout"), events, begins, res, rows, first, core)}


MODEL_PART = {"colour": "fragment", "draw": "total"}


def _malioc(mine, core, stage):
    """-> malioc's view of a shader's draws on `core`: price per thread (weighted by threads), bound pipes, registers,
    fp16 share, flags; None without prices."""
    k, n = ("px", "pixels") if stage == "frag" else ("vtx", "vertices")
    got = [(r, ((r.get("cost") or {}).get(core) or {})) for r in mine]
    got = [(r, c) for r, c in got if c.get(f"{k}_price") is not None]
    if not got:
        return None
    w = sum(r.get(n) or 0 for r, _ in got)
    price = (sum((r.get(n) or 0) * c[f"{k}_price"] for r, c in got) / w if w
             else sum(c[f"{k}_price"] for _, c in got) / len(got))
    fp16 = [c["px_fp16"] for _, c in got if k == "px" and c.get("px_fp16") is not None]
    return {"price": round(price, 3), "bound": sorted({b for _, c in got for b in c.get(f"{k}_bound") or []}),
            "regs": max((c.get(f"{k}_regs") or 0) for _, c in got), "fp16": min(fp16) if fp16 else None,
            "flags": sorted({f for _, c in got for f in c.get("flags") or []})}


def shader_cost(ko, events, begins, passes, rows, first, core):
    """dev["knockout"] (rd_device.knockout) -> {"error", "frame", "shaders": [{"stage", "mode", "shader", "names",
    "file", "draws", "pixels", "vertices", "model", "exec", "noise", "pipes", "passes", "error"}]} by measured cycles:
    base minus the replay with the shader swapped for its stub, over the passes it draws in (later passes reading its
    output are not its cost; issue cycles are exact, Execution core active moves by "noise" between replays). model:
    the same part of its draws priced by the model on `core`. A knockout of core/sweep.py (Streamline) has one pass,
    "frame", and ranks by the busiest unit."""
    if not ko:
        return None
    if ko.get("error"):
        return {"error": ko["error"], "shaders": []}
    names, base, spread = ko["names"], ko["base"], ko["spread"]
    ex = names.index(EXEC)
    pipe = {k: names.index(n) for k, n in PIPES.items() if n in names}
    pname = {"frame": "весь frame", **{str(p["event"]): p["name"] for p in passes}}
    by_call = {e: ev.get("shader") or "—" for ev in events for e in (ev.get("rd") or {}).get("events") or []}
    priced = {first.get(r["index"]): r for r in rows if r["kind"] != "tile"}
    frame = sum((v[ex] or 0) for v in base.values())
    out = []
    for sh in ko["shaders"]:
        seen = {}
        for e in sh["events"]:
            if e in by_call:
                seen[by_call[e]] = seen.get(by_call[e], 0) + 1
        mine = [priced[e] for e in sh["events"] if e in priced]
        part = MODEL_PART.get(sh.get("mode", "colour"))
        stages = {}
        for r in mine:
            stages[r["stage"]] = stages.get(r["stage"], 0) + 1
        model = [((r.get("cost") or {}).get(core) or {}).get(part) for r in mine] if part else []
        row = {"stage": sh.get("stage", "frag"), "mode": sh.get("mode", "colour"),
               "shader": max(seen, key=seen.get) if seen else sh["file"], "names": seen, "file": sh["file"],
               "events": sh["events"], "draws": len(sh["events"]), "pixels": sum(r.get("pixels") or 0 for r in mine),
               "vertices": sum(r.get("vertices") or 0 for r in mine),
               "model": sum(model) if model and None not in model else None,
               "malioc": _malioc(mine, core, sh.get("stage", "frag")),
               "draw_stage": "+".join(sorted(stages)) or None,
               "error": sh.get("error"), "exec": 0.0, "noise": 0.0, "pipes": {k: 0.0 for k in pipe}, "passes": []}
        out.append(row)
        own = ["frame"] if "frame" in base else sorted(
            {str(begins[bisect.bisect_right(begins, e) - 1]) for e in sh["events"] if begins and e >= begins[0]}, key=int)
        for p in own:
            b, k = base.get(p), (sh.get("passes") or {}).get(p)
            if not b or not k or b[ex] is None or k[ex] is None:
                continue
            d_pipe = {n: (b[i] or 0) - (k[i] or 0) for n, i in pipe.items()}
            d = b[ex] - k[ex]
            row["exec"] += d
            row["noise"] += spread.get(p) or 0
            for n in pipe:
                row["pipes"][n] += d_pipe[n]
            row["passes"].append({"event": None if p == "frame" else int(p), "name": pname.get(p, p), "exec": d,
                                  "base": b[ex]})
        if not seen and row["passes"]:
            row["shader"] = f"{row['passes'][0]['name']}: {sh['file']}"
    streamline = ko.get("source") == "streamline"  # its exec moves with the replay's own copies: rank by the units
    out.sort(key=lambda r: -(max(r["pipes"].values(), default=0) if streamline else r["exec"]))
    unit = max((sum((v[i] or 0) for v in base.values()) for i in pipe.values()), default=0)
    return {"error": None, "frame": frame, "frame_unit": unit, "shaders": out, "source": ko.get("source") or "renderdoc"}

