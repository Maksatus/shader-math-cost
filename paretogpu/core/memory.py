"""Memory traffic of the frame's render passes and a rough roofline (plan item K2.3): is ALU the bottleneck at all?

Source: the snapshot's RenderDoc capture (adapters/renderdoc/rd_memory.py): every texture with its size, format and
bytes, and how every call used it (sampled, colour / depth target, cleared, copied). Only the calls of the frame
count: from the first to the last call of a Frame Debugger event, and the clears right before the first.

A pass is a run of consecutive draws with the same colour and depth targets (Unity's native render pass on a tiled
GPU: "(RP 1:0) Draw Opaque" and "(RP 1:1) Draw Skybox" are one). On Mali the fragments live in tile memory; DRAM
sees per pass:
  load      a target that holds pixels written earlier in the frame and is not cleared before the pass: read whole;
  store     a target whose pixels are used after the pass (sampled, copied, loaded by a later pass, also outside the
            frame: the editor showing the Game view): written whole; a target nobody uses again is not stored
            (memoryless). What the frame needs, not what Unity's load / store actions say (Direct3D 11 in the editor
            has none);
  textures  every sampled texture of every call: min(shader runs x bytes per texel, the texture's bytes): one texel
            per fragment from the right mip, read no more than once per call (minification without mips and
            sampler state — K2.2 — are not seen);
  copies    a copy reads its source and writes its destination whole.
Depth + stencil counts 5 bytes a texel (Direct3D 11 pads it to 8). Targets (render textures) are AFBC-compressed on Mali: their loads, stores and samples count AFBC times their size.
Textures of assets: the editor (Direct3D 11) holds them uncompressed (RGBA8), the device as ASTC: an 8-bit RGBA
texture that is never a target counts ASSET_BITS bits per texel (ASTC 6x6; the real format per texture is K2.2).
Vertex and index buffers are not counted (K2.4).

Roofline of a pass on a core: shader time = its cycles (core/frame_cost, the events of the pass) / (shader cores x
clock), memory time = bytes / bandwidth of the device (model/cores.DEVICES); the pass is memory bound when the
memory time is longer. The frame: the sum over passes of the longer of the two.

  passes(textures, usage, actions, events, rows, cores) -> {"passes", "totals", "assumptions"}
"""
import re
from collections import defaultdict

from paretogpu.model.cores import DEVICES

AFBC = 0.5          # AFBC compression of render targets (to check on the device: ~0.5 for game frames)
ASSET_BITS = 3.56   # ASTC 6x6, Unity's default for Android textures
DEPTH_BYTES = {"D32S8": 5}  # Direct3D 11 pads 32-bit depth + stencil to 8 bytes; Mali keeps 4 + 1
UNCOMPRESSED_8BIT = ("R8G8B8A8", "B8G8R8A8", "R8G8B8X8", "B8G8R8X8")
READS = {"PS_Resource": "ps", "VS_Resource": "vs", "CS_Resource": "cs", "All_Resource": "ps",
         "GS_Resource": "vs", "HS_Resource": "vs", "DS_Resource": "vs", "TS_Resource": "vs", "MS_Resource": "vs"}
RW = ("PS_RWResource", "CS_RWResource", "VS_RWResource", "All_RWResource")
_RP = re.compile(r"\(RP (\d+):(\d+)\) ([^/]+)")


def texels(t):
    """Texels of the texture's whole mip chain (all layers and samples)."""
    w, h, d, n = t["width"], t["height"], max(1, t.get("depth") or 1), 0
    for m in range(max(1, t.get("mips") or 1)):
        n += max(1, w >> m) * max(1, h >> m) * max(1, d >> m)
    return n * max(1, t.get("arrays") or 1) * max(1, t.get("samples") or 1)


def is_target(t):
    return bool(t.get("color_target") or t.get("depth_target"))


def device_size(t):
    """(bytes per texel of the top mip on the device, bytes of the whole texture on the device, how it was found)."""
    bpt = t["bytes"] / max(1, texels(t))
    fmt = t.get("format") or ""
    real = next((b for k, b in DEPTH_BYTES.items() if fmt.startswith(k)), None)
    if real and real < bpt:
        bpt, t = real, {**t, "bytes": real * texels(t)}
    if is_target(t):
        return bpt * AFBC, t["bytes"] * AFBC, "afbc"
    if fmt.startswith(UNCOMPRESSED_8BIT):
        dev = ASSET_BITS / 8
        return dev, t["bytes"] * dev / bpt, "astc"
    return bpt, t["bytes"], "capture"


def _frame_calls(actions, events):
    """The capture's actions of the frame, in order: from the first to the last call of a Frame Debugger event, and
    the clears and copies right before the first."""
    eids = sorted(e for ev in events for e in ((ev.get("rd") or {}).get("events") or []))
    if not eids:
        return []
    lo, hi = eids[0], eids[-1]
    order = sorted(actions, key=lambda a: a["event"])
    start = next((i for i, a in enumerate(order) if a["event"] >= lo), len(order))
    while start > 0 and not ({"draw", "dispatch"} & set(order[start - 1]["kinds"])):
        start -= 1
    return [a for a in order[start:] if a["event"] <= hi and ({"draw", "dispatch", "clear", "copy", "resolve"}
                                                              & set(a["kinds"]))]


def _pass_name(names):
    seen = list(dict.fromkeys(n for n in names if n))
    return " + ".join(seen[:3]) + (f" +{len(seen) - 3}" if len(seen) > 3 else "") if seen else "—"


def passes(textures, usage, actions, events, rows, cores):
    """The passes of the frame with their bytes, the cycles of their events on every core and the roofline.
    textures / usage: rd_memory.py's output; actions: rd_actions.json "actions"; events: the normalized events with
    ev["rd"] (core/events.match); rows: frame_cost.compute()["events"] (the cost of every event on every core)."""
    tex = {int(t["id"]): t for t in textures}
    by_event = defaultdict(lambda: defaultdict(list))  # eid -> usage -> [texture id]
    for tid, us in usage.items():
        for eid, u in us:
            by_event[int(eid)][u].append(int(tid))
    calls = _frame_calls(actions, events)
    if not calls:
        return None
    hi = calls[-1]["event"]
    owner = {e: ev for ev in events for e in ((ev.get("rd") or {}).get("events") or [])}
    cost = {r["index"]: r for r in rows}

    def used_after(tid, eid):
        """Whether the pixels of the texture are used after event eid (in or after the frame): sampled, copied,
        resolved, or drawn into without a clear first."""
        for e, u in sorted((int(e), u) for e, u in usage.get(str(tid), usage.get(tid, []))):
            if e <= eid:
                continue
            if u == "Clear" or u == "CopyDst":
                return False
            return True
        return False

    out, cur = [], None
    written, cleared = set(), set()
    loose = {"copy": 0.0, "events": []}
    for a in calls:
        eid, use = a["event"], by_event.get(a["event"], {})
        if "clear" in a["kinds"]:
            cleared.update(use.get("Clear", []))
            continue
        if {"copy", "resolve"} & set(a["kinds"]):
            for tid in use.get("CopySrc", []) + use.get("ResolveSrc", []):
                loose["copy"] += device_size(tex[tid])[1] if tid in tex else 0
            for tid in use.get("CopyDst", []) + use.get("ResolveDst", []):
                if tid in tex:
                    loose["copy"] += device_size(tex[tid])[1]
                    written.add(tid)
                    cleared.discard(tid)
            loose["events"].append(eid)
            continue
        color = tuple(sorted(use.get("ColorTarget", [])))
        depth = tuple(sorted(use.get("DepthStencilTarget", [])))
        key = ("draw", color, depth) if "draw" in a["kinds"] else ("dispatch", eid)
        if cur is None or cur["key"] != key:
            cur = {"key": key, "calls": [], "targets": [], "tex": defaultdict(float), "names": [], "first": eid}
            for tid in color + depth:
                t = tex.get(tid)
                if t is None:
                    continue
                load = tid in written and tid not in cleared
                cur["targets"].append({"id": tid, "name": t["name"], "width": t["width"], "height": t["height"],
                                       "format": t["format"], "samples": t.get("samples") or 1,
                                       "depth": tid in depth, "cleared": tid in cleared, "load": load,
                                       "bytes": device_size(t)[1]})
                cleared.discard(tid)
            out.append(cur)
        cur["calls"].append(eid)
        ev = owner.get(eid)
        m = _RP.search((ev or {}).get("path") or "")
        cur["names"].append(m.group(3) if m else (a["path"].split("/")[-1] if a["path"] else a["name"]))
        runs = {"ps": a.get("ps_invocations") or 0, "vs": a.get("vs_invocations") or 0,
                "cs": a.get("cs_invocations") or 0}
        for u, stage in READS.items():
            for tid in use.get(u, []):
                t = tex.get(tid)
                if t is None or tid in color + depth:
                    continue
                per, whole, _ = device_size(t)
                cur["tex"][tid] += min(runs[stage] * per, whole)
        for u in RW:
            for tid in use.get(u, []):
                if tid in tex:
                    cur["tex"][tid] += device_size(tex[tid])[1]
                    written.add(tid)
        written.update(color + depth)

    # what every pass costs: stores once its last call is known, the cycles of its events split by their calls
    share = defaultdict(lambda: defaultdict(float))  # pass index -> event index -> share of the event's calls
    for i, p in enumerate(out):
        for eid in p["calls"]:
            ev = owner.get(eid)
            if ev:
                share[i][ev["index"]] += 1 / len(ev["rd"]["events"])
    res = []
    for i, p in enumerate(out):
        last = p["calls"][-1]
        for t in p["targets"]:
            t["store"] = used_after(t["id"], last)
        load = sum(t["bytes"] for t in p["targets"] if t["load"])
        store = sum(t["bytes"] for t in p["targets"] if t["store"])
        texb = sum(p["tex"].values())
        total = load + store + texb
        cycles = {c: sum(f * ((cost.get(e) or {}).get("cost", {}).get(c) or {}).get("total", 0.0)
                         for e, f in share[i].items()) for c in cores}
        top = sorted(p["tex"].items(), key=lambda kv: -kv[1])[:6]
        res.append({
            "index": i, "name": _pass_name(p["names"]), "kind": p["key"][0], "first_call": p["first"],
            "calls": len(p["calls"]), "events": sorted(share[i]),
            "targets": [{k: v for k, v in t.items() if k != "id"} for t in p["targets"]],
            "bytes": {"load": load, "store": store, "textures": texb, "total": total},
            "textures": [{"name": tex[tid]["name"], "format": tex[tid]["format"],
                          "size": [tex[tid]["width"], tex[tid]["height"]], "mips": tex[tid].get("mips"),
                          "how": device_size(tex[tid])[2], "bytes": b} for tid, b in top],
            "cycles": cycles, "roofline": {c: roofline(cycles[c], total, c) for c in cores}})
    frame_bytes = sum(p["bytes"]["total"] for p in res) + loose["copy"]
    for p in res:
        p["share"] = p["bytes"]["total"] / frame_bytes if frame_bytes else 0.0
    totals = {"bytes": {k: sum(p["bytes"][k] for p in res) for k in ("load", "store", "textures", "total")},
              "copies": loose["copy"], "copy_calls": len(loose["events"])}
    totals["bytes"]["total"] += loose["copy"]
    totals["roofline"] = {}
    for c in cores:
        if c not in DEVICES:
            continue
        r = [p["roofline"][c] for p in res if p["roofline"].get(c)]
        alu = sum(x["alu_ms"] for x in r)
        mem = sum(x["mem_ms"] for x in r) + roofline(0.0, loose["copy"], c)["mem_ms"]
        frame = sum(max(x["alu_ms"], x["mem_ms"]) for x in r) + roofline(0.0, loose["copy"], c)["mem_ms"]
        bound = [p for p in res if (p["roofline"].get(c) or {}).get("bound") == "memory"]
        totals["roofline"][c] = {"alu_ms": alu, "mem_ms": mem, "frame_ms": frame,
                                 "memory_passes": len(bound),
                                 "memory_cycles_share": (sum(p["cycles"][c] for p in bound) /
                                                         sum(p["cycles"][c] for p in res))
                                 if sum(p["cycles"][c] for p in res) else 0.0}
    return {"passes": res, "totals": totals,
            "assumptions": {"afbc": AFBC, "asset_bits": ASSET_BITS,
                            "devices": {c: DEVICES[c] for c in cores if c in DEVICES}}}


def roofline(cycles, nbytes, core):
    """{"alu_ms", "mem_ms", "bound": "alu" | "memory"} of work on the core's device, or None (no device known)."""
    d = DEVICES.get(core)
    if not d:
        return None
    alu = cycles / (d["cores"] * d["mhz"] * 1e6) * 1e3
    mem = nbytes / (d["gbps"] * 1e9) * 1e3
    return {"alu_ms": alu, "mem_ms": mem, "bound": "memory" if mem > alu else "alu"}
