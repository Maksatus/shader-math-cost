"""DRAM traffic of the frame's render passes (RenderDoc usage, adapters/renderdoc/rd_memory.py) and a roofline.

A pass = consecutive draws of one camera's "(RP n:m)" n, subpasses merged (targets join when first bound; sampling a
pass target is a tile read); without the marker, a run with the same colour + depth targets. Mali DRAM per pass:
  load      a target written earlier in the frame and not cleared before its first use in the pass (one layer, top mip)
  store     a target whose pixels are used later (incl. outside the frame); decided by what the frame needs, not by
            Unity's load / store actions (D3D11 in the editor has none); unused again = memoryless, not stored
  textures  per sampled texture per call: min(shader runs x bytes per texel, whole texture); whole without run counts
  copies    source + destination whole; a resolve of the current pass's target right after it is on tile (destination
            written with the pass, source not read / stored); a resolve of a cleared-only target clears its
            destination (editor D3D11 resolves); a copy touching the current pass's targets ends the pass
Sizes: editor textures at most their format's bytes (desktop drivers pad: D32S8 to 8, MSAA); targets x AFBC; editor RGBA8 non-target assets count as ASTC
(ASSET_BITS); device-frame ("native") textures as captured. Vertex and index buffers are not counted.
Pass cycles: its events' cycles split by each call's share of the event's invocations, so passes sum to the frame;
Mali's own tile threads (tile_threads) are frame_cost rows of kind "tile", not in these pass cycles.
Frame time = sum over passes of max(alu_ms, mem_ms).
"""
import re
from collections import defaultdict

from paretogpu.model.cores import DEVICES

AFBC = 0.5          # assumed AFBC ratio of render targets (~0.5 for game frames, not verified on device)
ASSET_BITS = 3.56   # ASTC 6x6, Unity's default for Android textures
_CHANNEL_BITS = re.compile(r"[RGBAXDSE](\d+)")
UNCOMPRESSED_8BIT = ("R8G8B8A8", "B8G8R8A8", "R8G8B8X8", "B8G8R8X8")
READS = {"PS_Resource": "ps", "VS_Resource": "vs", "CS_Resource": "cs", "All_Resource": "ps",
         "GS_Resource": "vs", "HS_Resource": "vs", "DS_Resource": "vs", "TS_Resource": "vs", "MS_Resource": "vs"}
RW = ("PS_RWResource", "CS_RWResource", "VS_RWResource", "All_RWResource")
_RP = re.compile(r"\(RP (\d+):(\d+)\) ([^/]+)")
NOT_A_USE = ("Barrier",)
OVERWRITE = ("Clear", "CopyDst", "Discard", "CPUWrite")


def texels(t):
    w,h, d, n = t["width"], t["height"], max(1, t.get("depth") or 1), 0
    for m in range(max(1, t.get("mips") or 1)):
        n += max(1, w >> m) * max(1, h >> m) * max(1, d >> m)
    return n * max(1, t.get("arrays") or 1) * max(1, t.get("samples") or 1)


def is_target(t):
    return bool(t.get("color_target") or t.get("depth_target"))


def format_bytes(fmt):
    """-> bytes per texel of an uncompressed format by its channel bits ("R11G11B10_FLOAT" 4, "D32S8" 5), else None."""
    bits = sum(int(b) for b in _CHANNEL_BITS.findall(fmt.split("_")[0]))
    return bits / 8 if bits else None


def device_size(t):
    """-> (device bytes per texel of top mip, device bytes of whole texture, "afbc" | "astc" | "capture").
    An editor texture's byteSize is the desktop driver's allocation (D32S8 padded to 8 B, NVIDIA MSAA 4x 16 B per
    4 B sample): it counts at most its format's size."""
    bpt = t["bytes"] / max(1, texels(t))
    fmt = t.get("format") or ""
    real = None if t.get("native") else format_bytes(fmt)
    if real and real < bpt:
        bpt, t = real, {**t, "bytes": real * texels(t)}
    if is_target(t):
        return bpt * AFBC, t["bytes"] * AFBC, "afbc"
    if fmt.startswith(UNCOMPRESSED_8BIT) and not t.get("native"):
        dev = ASSET_BITS / 8
        return dev, t["bytes"] * dev / bpt, "astc"
    return bpt, t["bytes"], "capture"


def _frame_calls(actions, events):
    """First..last call of a Frame Debugger event, plus the clears / copies right before it that have no path or one
    under an event's root marker (excludes the editor's own work before the frame)."""
    eids = sorted(e for ev in events for e in ((ev.get("rd") or {}).get("events") or []))
    if not eids:
        return []
    lo, hi = eids[0], eids[-1]
    kinds = {"draw", "dispatch", "clear", "copy", "resolve"}
    order = sorted(actions, key=lambda a: a["event"])
    start = next((i for i, a in enumerate(order) if a["event"] >= lo), len(order))
    first = start
    while first > 0 and not ({"draw", "dispatch"} & set(order[first - 1]["kinds"])):
        first -= 1
    roots = {(ev.get("path") or "").split("/")[0] for ev in events if ev.get("rd")} - {""}
    before = [a for a in order[first:start] if not a.get("path") or any(r in a["path"] for r in roots)]
    return [a for a in before + order[start:] if a["event"] <= hi and kinds & set(a["kinds"])]


def target_size(t):
    """Device bytes of one layer of the top mip: a pass draws into one layer and mip."""
    return device_size(t)[0] * t["width"] * t["height"] * max(1, t.get("samples") or 1)


def _call_share(ev, eid, by_action):
    """-> {"fragment" | "vertex" | "compute": share of the event's work done by call eid}: by invocations, else
    1 / calls."""
    calls = ev["rd"]["events"]
    out = {}
    for part, counter in (("fragment", "ps_invocations"), ("vertex", "vs_invocations"), ("compute", "cs_invocations")):
        runs = [(by_action.get(e) or {}).get(counter) for e in calls]
        total = sum(r or 0 for r in runs)
        mine = (by_action.get(eid) or {}).get(counter)
        out[part] = (mine or 0) / total if total and None not in runs else 1 / len(calls)
    return out


def _pass_key(ev, color, depth):
    path =(ev or {}).get("path") or ""
    m = _RP.search(path)
    return ("draw", "rp", path[:m.start()], m.group(1)) if m else ("draw", "targets", color, depth)


def _pass_name(names):
    seen = list(dict.fromkeys(n for n in names if n))
    return " + ".join(seen[:3]) + (f" +{len(seen) - 3}" if len(seen) > 3 else "") if seen else "—"


def passes(textures, usage, actions, events, rows, cores):
    """textures / usage: rd_memory.py output; actions: rd_actions.json "actions"; events: with ev["rd"]
    (core/events.match); rows: frame_cost.compute()["events"]. -> {"passes", "totals", "assumptions"} or None."""
    tex = {int(t["id"]): t for t in textures}
    by_event = defaultdict(lambda: defaultdict(list))  # eid -> usage -> [texture id]
    for tid, us in usage.items():
        for eid, u in us:
            by_event[int(eid)][u].append(int(tid))
    calls = _frame_calls(actions, events)
    if not calls:
        return None
    by_action = {a["event"]: a for a in actions}
    hi = calls[-1]["event"]
    owner = {e: ev for ev in events for e in ((ev.get("rd") or {}).get("events") or [])}
    cost = {r["index"]: r for r in rows}

    tile_resolves = set()

    def used_after(tid, eid):
        """A device-frame swapchain image is always used: the present is not in the capture's usage."""
        if tex.get(int(tid), {}).get("swapchain") and tex[int(tid)].get("native"):
            return True
        for e, u in sorted((int(e), u) for e, u in usage.get(str(tid), usage.get(tid, []))):
            if e <= eid or (u == "ResolveSrc" and e in tile_resolves) or u in NOT_A_USE:
                continue
            if u in OVERWRITE:
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
            src = use.get("CopySrc", []) + use.get("ResolveSrc", [])
            dst = [tid for tid in use.get("CopyDst", []) + use.get("ResolveDst", []) if tid in tex]
            on_tile = ("resolve" in a["kinds"] and "copy" not in a["kinds"] and cur is not None
                       and cur["key"][0] == "draw" and src and set(src) <= cur["ids"])
            if src and set(src) <= cleared:
                cleared.update(dst)
                continue
            if on_tile:
                tile_resolves.add(eid)
                cur["resolves"].extend(tex[tid]["name"] for tid in dst)
                cur["resolve_bytes"] += sum(device_size(tex[tid])[1] for tid in dst)
            else:
                loose["copy"] += sum(device_size(tex[tid])[1] for tid in src if tid in tex)
                loose["copy"] += sum(device_size(tex[tid])[1] for tid in dst)
                loose["events"].append(eid)
                if cur is not None and (set(src) | set(dst)) & cur["ids"]:
                    cur = None
            written.update(dst)
            cleared.difference_update(dst)
            continue
        color = tuple(sorted(use.get("ColorTarget", [])))
        depth = tuple(sorted(use.get("DepthStencilTarget", [])))
        ev = owner.get(eid)
        key = _pass_key(ev, color, depth) if "draw" in a["kinds"] else ("dispatch", eid)
        if (key[:2] == ("draw", "targets") and cur is not None and cur["key"][:2] == ("draw", "rp")
                and color + depth and set(color + depth) <= cur["ids"]):
            key = cur["key"]
        if cur is None or cur["key"] != key:
            cur = {"key": key, "calls": [], "targets": [], "ids": set(), "tex": defaultdict(float), "names": [],
                   "first": eid, "resolves": [], "resolve_bytes": 0.0}
            out.append(cur)
        for tid in color + depth:
            t = tex.get(tid)
            if t is None or tid in cur["ids"]:
                continue
            cur["ids"].add(tid)
            cur["targets"].append({"id": tid, "name": t["name"], "width": t["width"], "height": t["height"],
                                   "format": t["format"], "samples": t.get("samples") or 1,
                                   "depth": tid in depth, "cleared": tid in cleared,
                                   "load": tid in written and tid not in cleared, "bytes": target_size(t)})
            cleared.discard(tid)
        cur["calls"].append(eid)
        m = _RP.search((ev or {}).get("path") or "")
        cur["names"].append(m.group(3) if m else (a["path"].split("/")[-1] if a["path"] else a["name"]))
        runs = {"ps": a.get("ps_invocations"), "vs": a.get("vs_invocations"), "cs": a.get("cs_invocations")}
        for u, stage in READS.items():
            for tid in use.get(u, []):
                t = tex.get(tid)
                if t is None or tid in cur["ids"]:
                    continue
                per, whole, _ = device_size(t)
                cur["tex"][tid] += whole if runs[stage] is None else min(runs[stage] * per, whole)
        for u in RW:
            for tid in use.get(u, []):
                if tid in tex:
                    cur["tex"][tid] += device_size(tex[tid])[1]
                    written.add(tid)
        written.update(color + depth)
        cleared.difference_update(color + depth)

    # stores need the pass's last call, so they are decided after all passes are built
    share = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))  # pass -> event -> part -> share
    for i, p in enumerate(out):
        for eid in p["calls"]:
            ev = owner.get(eid)
            if ev:
                for part, f in _call_share(ev, eid, by_action).items():
                    share[i][ev["index"]][part] += f
    res = []
    for i, p in enumerate(out):
        last = p["calls"][-1]
        for t in p["targets"]:
            t["store"] = used_after(t["id"], last)
        load = sum(t["bytes"] for t in p["targets"] if t["load"])
        store = sum(t["bytes"] for t in p["targets"] if t["store"]) + p["resolve_bytes"]
        texb = sum(p["tex"].values())
        total = load + store + texb
        parts = {c: {k: sum(f[k] * ((cost.get(e) or {}).get("cost", {}).get(c) or {}).get(k, 0.0)
                            for e, f in share[i].items()) for k in ("fragment", "vertex", "compute")} for c in cores}
        cycles = {c: sum(parts[c].values()) for c in cores}
        top = sorted(p["tex"].items(), key=lambda kv: -kv[1])[:6]
        res.append({
            "index": i, "name": _pass_name(p["names"]), "kind": p["key"][0], "first_call": p["first"],
            "calls": len(p["calls"]), "events": sorted(share[i]),
            "targets": [{k: v for k, v in t.items() if k != "id"} for t in p["targets"]], "resolves": p["resolves"],
            "bytes": {"load": load, "store": store, "textures": texb, "total": total},
            "textures": [{"name": tex[tid]["name"], "format": tex[tid]["format"],
                          "size": [tex[tid]["width"], tex[tid]["height"]], "mips": tex[tid].get("mips"),
                          "how": device_size(tex[tid])[2], "bytes": b} for tid, b in top],
            "cycles": cycles, "parts": parts, "roofline": {c: roofline(cycles[c], total, c) for c in cores}})
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
                            "native": any(t.get("native") for t in textures),
                            "devices": {c: DEVICES[c] for c in cores if c in DEVICES}}}


TILE_PRICE = 2.1  # cycles per Mali tile thread on G78 (K3.1): 2.12 in a clear + resolve pass without draws, 2.07 in the
# transparent pass that loads MSAA colour + depth
_BEGIN, _END = "vkCmdBeginRenderPass", "vkCmdEndRenderPass"
_OPS = re.compile(r"\b(C|DS)=([A-Za-z' ]+?)[,)]")
_ONE_OP = re.compile(r"\((Load|Clear|Don't Care)\)")


def _frame_span(actions, events):
    """(first, last) event of the frame: its calls, back over the passes and clears right before the first one."""
    eids = sorted(e for ev in events for e in ((ev.get("rd") or {}).get("events") or []))
    if not eids:
        return None
    order = sorted(actions, key=lambda a: a["event"])
    first = next((i for i, a in enumerate(order) if a["event"] >= eids[0]), len(order))
    while first > 0 and not ({"draw", "dispatch"} & set(order[first - 1]["kinds"])):
        first -= 1
    return (order[first]["event"] if first < len(order) else eids[0]), eids[-1]


def tile_threads(textures, usage, actions, events=None):
    """Fragment threads Mali's driver runs in each Vulkan render pass (G78 fragment-thread counters, K3.1): one per
    sample of a multisampled colour target (cleared or loaded), one per pixel of its resolve on tile, one per sample of a
    loaded depth / stencil target; a loaded single-sample colour target needs none. events: only the passes of the
    frame, not the editor's around it.
    -> [{"event", "path", "threads", "parts": [[what, texture, threads]]}] for passes with threads."""
    span = _frame_span(actions, events) if events else None
    tex = {int(t["id"]): t for t in textures}
    at = defaultdict(lambda: defaultdict(set))
    for tid, us in usage.items():
        for eid, u in us:
            at[int(eid)][u].add(int(tid))
    order = sorted(actions, key=lambda a: a["event"])
    out, begin = [], None
    for a in order:
        name = a.get("name") or ""
        if name.startswith(_BEGIN):
            begin = a if not span or span[0] <= a["event"] <= span[1] else None
        elif name.startswith(_END) and begin is not None:
            b, e = begin["event"], a["event"]
            use = defaultdict(set)
            for eid in range(b, e + 1):
                for u, ids in at.get(eid, {}).items():
                    use[u] |= ids
            ops = dict(_OPS.findall(begin["name"]))
            one = _ONE_OP.search(begin["name"])
            depth_load = (ops.get("DS") or (one.group(1) if one else "")) == "Load"
            depth = {i for i in use["DepthStencilTarget"] | use["Clear"] if (tex.get(i) or {}).get("depth_target")
                     or (tex.get(i) or {}).get("format", "").startswith(("D16", "D24", "D32", "S8"))}
            color = (use["ColorTarget"] | use["Clear"] | use["ResolveSrc"]) - depth
            parts = []
            for i in sorted(color):
                t = tex.get(i)
                if t and (t.get("samples") or 1) > 1:
                    parts.append(["msaa", t["name"], t["samples"] * t["width"] * t["height"]])
            for i in sorted(use["ResolveDst"] & at.get(e, {}).get("ResolveDst", set())):
                t = tex.get(i)
                if t:
                    parts.append(["resolve", t["name"], t["width"] * t["height"]])
            if depth_load:
                for i in sorted(depth):
                    t = tex.get(i)
                    if t:
                        parts.append(["depth_load", t["name"], (t.get("samples") or 1) * t["width"] * t["height"]])
            if parts:
                out.append({"event": b, "path": begin.get("path") or a.get("path") or "",
                            "threads": sum(p[2] for p in parts), "parts": parts})
            begin = None
    return out


def roofline(cycles, nbytes, core):
    d =DEVICES.get(core)
    if not d:
        return None
    alu = cycles / (d["cores"] * d["mhz"] * 1e6) * 1e3
    mem = nbytes / (d["gbps"] * 1e9) * 1e3
    return {"alu_ms": alu, "mem_ms": mem, "bound": "memory" if mem > alu else "alu"}
