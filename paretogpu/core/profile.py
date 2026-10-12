"""The settings a frame snapshot is drawn with (plan item K1.12), for any project and render pipeline.

The snapshot (cs/ParetoGpuFrame.cs) writes the quality level, build target, Game view size and the active render
pipeline asset as its SerializedObject shows it, with the ScriptableObjects it refers to (renderers and their
features in URP). What a project sets elsewhere at run time (its own quality profiles) is not in the asset, so what
the frame was really drawn with is read from the frame itself: its scale is the size of its main render target over
the Game view, and its effects are its passes.

describe(meta, size) -> what the snapshot says (None where it says nothing); warnings(info) -> why the frame may not
be drawn as on a Mali device, in the report's language.
"""
import re
from collections import Counter

MOBILE_TARGETS = ("Android",)  # build targets of devices with Mali cores
SCALE_SLACK = 0.1              # the asset's render scale and the frame's differ by more than this share: say so


def frame_size(rows):
    """[width, height] of the render target with the most pixels shaded into it, or None.
    rows: [{"rt_size": [w, h], "pixels": n}] (the events of a cost run)."""
    px = Counter()
    for r in rows or []:
        w, h = (r.get("rt_size") or [None, None])[:2]
        if w and h:
            px[(w, h)] += r.get("pixels") or 0
    return list(px.most_common(1)[0][0]) if px else None


def _key(path):
    return re.sub(r"^m_", "", path.rsplit(".", 1)[-1]).replace("_", "").lower()


def _asset_scale(pipeline):
    """The render scale field of the pipeline asset itself (URP: m_RenderScale), or None."""
    for path, v in ((pipeline or {}).get("fields") or {}).items():
        if _key(path) == "renderscale" and isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _features(pipeline):
    """[{"name", "class", "active"}] of the objects under the asset that can be switched off (URP renderer features:
    m_Active)."""
    out, todo = [], list((pipeline or {}).get("refs") or [])
    while todo:
        o = todo.pop(0)
        active = (o.get("fields") or {}).get("m_Active")
        if isinstance(active, bool):
            out.append({"name": o.get("name"), "class": o.get("class"), "active": active})
        todo += o.get("refs") or []
    return out


def _frame_scale(size, screen):
    """The frame's main render target over the Game view, when both sides agree (else None: another aspect)."""
    if not size or not screen or not all(screen):
        return None
    sx, sy = size[0] / screen[0], size[1] / screen[1]
    return round((sx + sy) / 2, 3) if abs(sx - sy) <= 0.02 * max(sx, sy) else None


def describe(meta, size=None):
    """What the snapshot meta (frame_events.json without the events) and the frame size say about its settings."""
    p = meta.get("pipeline") or {}
    return {
        "recorded": "build_target" in meta, "quality": meta.get("quality"), "quality_level": meta.get("quality_level"),
        "build_target": meta.get("build_target"), "screen": meta.get("screen"), "frame_size": size,
        "pipeline": p.get("name"), "pipeline_class": p.get("class"),
        "renderers": [r.get("name") for r in p.get("refs") or []],
        "asset_scale": _asset_scale(p), "frame_scale": _frame_scale(size, meta.get("screen")),
        "features": _features(p), "fields": {k: v for k, v in (p.get("fields") or {}).items() if ".Array." not in k},
    }


def _size(s):
    return "×".join(map(str, s)) if s else "?"


def warnings(info):
    """Why the frame may not be drawn as on a Mali device: [text]."""
    if not info["recorded"]:
        return ["Снимок сделан до записи настроек кадра: неизвестно, в каком качестве и разрешении он снят. "
                "Снимите кадр заново."]
    out = []
    bt = info["build_target"]
    if bt and bt not in MOBILE_TARGETS:
        out.append(f"Платформа сборки в редакторе — {bt}, а не Android: качество, keywords и варианты кадра могут "
                   "быть не те, что на устройстве.")
    a, f = info["asset_scale"], info["frame_scale"]
    if a and f and abs(a - f) > SCALE_SLACK * max(a, f):
        out.append(f"RenderScale в ассете пайплайна {a:g}, а кадр нарисован в масштабе {f:g} от Game view "
                   f"({_size(info['frame_size'])} из {_size(info['screen'])}): масштаб задаётся не только ассетом, "
                   "верен масштаб кадра.")
    return out


def summary(info):
    """One line for the console."""
    if not info["recorded"]:
        return "settings: not recorded in this snapshot"
    parts = [f"quality {info['quality']}", f"build target {info['build_target']}"]
    if info["pipeline"]:
        parts.append(f"pipeline {info['pipeline']} ({info['pipeline_class']}"
                     + (f", renderScale {info['asset_scale']:g}" if info["asset_scale"] else "") + ")")
    parts.append(f"Game view {_size(info['screen'])}, frame {_size(info['frame_size'])}"
                 + (f" (scale {info['frame_scale']:g})" if info["frame_scale"] else ""))
    if info["features"]:
        parts.append("features: " + ", ".join(f"{x['name']}{'' if x['active'] else ' (off)'}" for x in info["features"]))
    return ", ".join(parts)
