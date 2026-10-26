"""Textures each draw reads (K2.2): the calls' lists from the capture (rd_actions.json "textures" [{"image", "stage",
"filter"}], rd_vulkan.py) joined with the capture's textures (rd_memory.json). Format: a device frame's as captured; a
PC frame's from the importer's Android format (the editor samples ASTC decoded as RGBA8), as captured when the texture
is not an asset. Both flags only for a texture the fragment stage minifies (not a render target, more texels than the
pixels of every call that reads it, which drew some): a magnified or tiny one stays in the texture cache whatever its
format.
  uncompressed  format without block compression
  no_mips       one mip"""
COMPRESSED = ("ASTC", "ETC", "EAC", "BC", "PVRTC", "DXT")


def compressed(fmt):
    return fmt.upper().startswith(COMPRESSED)


def _format(t, formats):
    """-> (format, source "capture" | "android" | "editor")."""
    if t.get("native"):
        return t["format"], "capture"
    found = sorted({x["format"] for x in (formats or {}).get(t["name"]) or []})
    if found:
        return " | ".join(found), "android"
    return t["format"], "capture" if formats is not None else "editor"


def of_draw(ev, calls, tex, formats):
    """ev: the row's event (ev["rd"]["events"]), calls {event: rd_actions action}, tex {id: rd_memory texture}.
    -> [{"name", "format", "source", "size", "mips", "cube", "target", "stage", "filter", "flags"}]."""
    seen, most = {}, {}
    for e in (ev.get("rd") or {}).get("events") or []:
        a = calls.get(e) or {}
        for x in a.get("textures") or []:
            k = (x["image"], x["stage"])
            if k not in seen or x["filter"]:
                seen[k] = x
            most[k] = max(most.get(k, 0), a.get("ps_invocations") or 0)
    out = []
    for (image, stage), x in seen.items():
        t = tex.get(image)
        if t is None:
            continue
        target = bool(t.get("color_target") or t.get("depth_target"))
        fmt, source = _format(t, formats)
        texels = t["width"] * t["height"] * (6 if t.get("cube") else 1)
        minified = not target and stage == "frag" and 0 < most[(image, stage)] < texels
        flags = []
        if minified and not any(compressed(f.strip()) for f in fmt.split("|")):
            flags.append("uncompressed")
        if minified and (t.get("mips") or 1) == 1:
            flags.append("no_mips")
        out.append({"name": t["name"], "format": fmt, "source": source, "size": [t["width"], t["height"]],
                    "mips": t.get("mips"), "cube": bool(t.get("cube")), "target": target, "stage": stage,
                    "filter": x["filter"], "flags": flags})
    return sorted(out, key=lambda x: (x["stage"] != "frag", x["target"], x["name"]))


def attach(rows, events, capture, formats):
    """Sets row["textures"] on every draw / dispatch row of the frame."""
    by_index = {e["index"]: e for e in events}
    calls = {a["event"]: a for a in capture["actions"]}
    tex = {int(t["id"]): t for t in capture["textures"]}
    for r in rows:
        ev = by_index.get(r["index"])
        if ev is not None and r["kind"] in ("draw", "compute"):
            r["textures"] = of_draw(ev, calls, tex, formats)


def asset_names(capture):
    """Names of the capture's textures that are not render targets: the ones an importer may have."""
    return sorted({t["name"] for t in capture["textures"]
                   if t["name"] and not (t.get("color_target") or t.get("depth_target") or t.get("swapchain"))})
