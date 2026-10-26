import re
from collections import Counter

SKIP = re.compile(r"^(vkCmd|ID3D|RenderLoop\.|Shadows\.|Camera\.|GUI\.|UI\.|Setup |PlayerEndOfFrame|FrameTime\.|"
                  r"Inl_|Clear Rendering State|ScriptableRenderer)")
_RP = re.compile(r"^\(RP \d+:\d+\) ")

MOBILE_TARGETS = ("Android",)


def markers(path):
    segs = (path or "").split("/")
    if "ExecuteRenderGraph" in segs:
        segs = segs[segs.index("ExecuteRenderGraph") + 1:]
    return [m for m in (_RP.sub("", s) for s in segs) if m and not SKIP.match(m)]


def frame_size(rows):
    """Target with the most opaque pixels: UI and the upscale are full size and would outweigh a scaled scene."""
    rows = rows or []
    opaque = [r for r in rows if r.get("stage") == "opaque"]
    px = Counter()
    for r in opaque or rows:
        w, h = (r.get("rt_size") or [None, None])[:2]
        if w and h:
            px[(w, h)] += r.get("pixels") or 0
    return list(px.most_common(1)[0][0]) if px else None


def _frame_scale(size, screen):
    if not size or not screen or not all(screen):
        return None
    sx, sy = size[0] / screen[0], size[1] / screen[1]
    return round((sx + sy) / 2, 3) if abs(sx - sy) <= 0.02 * max(sx, sy) else None


def describe(meta, size=None):
    return {
        "quality": meta.get("quality"), "android_quality": meta.get("android_quality"),
        "build_target": meta.get("build_target"), "screen": meta.get("screen"),
        "frame_size": size, "pipeline": (meta.get("pipeline") or {}).get("name"),
        "frame_scale": _frame_scale(size, meta.get("screen")),
        "device": meta.get("device"),
    }


def warnings(info):
    bt, out = info["build_target"], []
    if bt and bt not in MOBILE_TARGETS:
        out.append({"code": "build_target_not_mobile", "target": bt})
    android = info.get("android_quality")
    if android and info.get("quality") and info["quality"] not in android:
        out.append({"code": "quality_not_android", "quality": info["quality"], "android": ", ".join(android)})
    return out


def _size(s):
    return "×".join(map(str, s)) if s else "?"


def summary(info):
    dev = info.get("device")
    who = f"phone {dev.get('model')} ({dev.get('gpu') or 'GPU unknown'})" if dev else f"quality {info['quality']}"
    return (f"{who}, build target {info['build_target']}, {'screen' if dev else 'Game view'} {_size(info['screen'])}, "
            f"frame {_size(info['frame_size'])}" + (f" (scale {info['frame_scale']:g})" if info["frame_scale"] else ""))


def render(rows, memory=None, screen=None):
    """memory: core/memory.passes() result, for the main target's samples and format."""
    size = frame_size(rows)
    passes = []
    for r in sorted(rows or [], key=lambda r: r["index"]):
        m = markers(r.get("path"))[:1]
        if m and m[0] not in passes:
            passes.append(m[0])
    # the target the opaque pass draws most pixels into (not the first of its size: a prepass's normals come first)
    drawn = Counter()
    for r in rows or []:
        if r.get("stage") == "opaque" and r.get("rt"):
            drawn[r["rt"]] += r.get("pixels") or 0
    name = drawn.most_common(1)[0][0] if drawn else None
    main = next((t for p in (memory or {}).get("passes") or [] for t in p.get("targets") or []
                 if t.get("name") == name and not t.get("depth")), None)
    shadows = sorted({tuple(r["rt_size"]) for r in rows or []
                      if r.get("stage") == "shadow" and all(r.get("rt_size") or [])})
    return {"size": size, "scale": _frame_scale(size, screen), "msaa": (main or {}).get("samples"),
            "color": (main or {}).get("format"), "shadow_maps": [list(x) for x in shadows], "passes": passes}


def differences(a, b):
    out = []
    for k in ("size", "msaa", "color", "shadow_maps"):
        if (a or {}).get(k) != (b or {}).get(k):
            out.append({"what": k, "a": (a or {}).get(k), "b": (b or {}).get(k)})
    pa, pb = (a or {}).get("passes") or [], (b or {}).get("passes") or []
    if set(pa) != set(pb):
        out.append({"what": "passes", "a": [x for x in pa if x not in pb], "b": [x for x in pb if x not in pa]})
    return out
