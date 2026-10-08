"""Comparison of two frame cost runs (frame_cost.json of `cost`).

Every `cost` keeps its result (store/cost_runs.py), so a snapshot priced again after a shader change can be
compared with its earlier prices (the same pixels and vertices: only prices differ), and two snapshots of a project
with each other (the scene, camera and LOD may differ too).

Events are matched by their shader variant (shader | subshader.pass name | keywords), not by event index: indices
move between frames. The change of every variant B - A on a core is split into
  price = (pB - pA) * (wA + wB) / 2    cheaper or dearer per pixel / vertex / thread (the shader itself)
  work  = (wB - wA) * (pA + pB) / 2    more or fewer pixels / vertices / threads (scene, camera, LOD)
per stage (fragment: pixels x px_price, vertex: vertices x vtx_price, compute: threads x cs_price), where
p = cycles / work of the variant on that side; price + work = B - A exactly. A variant only in A or only in B
(keywords changed, shader added or removed) is `mix`. Shader rows are the sums of their variants.

compare(a, b) -> JSON-able dict for every common core; views/html.py puts it into result_view.html (the same
page in the UI and in the file `python -m paretogpu compare` writes).
"""
import os
import time
from collections import Counter

STAGES = (("fragment", "pixels", "px_price"), ("vertex", "vertices", "vtx_price"), ("compute", "threads", "cs_price"))
EVENTS_SHOWN = 12  # events of a variant listed in its details


# --- comparison --------------------------------------------------------------------------------------------------
def resolution(c):
    """The frame size: the render target with the most pixels shaded into it."""
    px = Counter()
    for e in c.get("events") or []:
        w, h = (e.get("rt_size") or [None, None])[:2]
        if w and h:
            px[(w, h)] += e.get("pixels") or 0
    return list(px.most_common(1)[0][0]) if px else None


def info(c):
    fd = c.get("frame_dir") or ""
    return {"snapshot": os.path.basename(fd.rstrip("\\/")) or None, "frame_dir": fd,
            "project": (c.get("frame") or {}).get("project"), "computed_at": c.get("computed_at"),
            "api": c.get("api"), "malioc": c.get("malioc"), "main_core": c.get("main_core"),
            "cores": [x["name"] for x in c.get("cores") or []], "loops_n": (c.get("loops") or {}).get("n"),
            "resolution": resolution(c), "checked": c.get("variants_checked"),
            "unpriced_draws": sum(1 for m in c.get("missing") or [] if m.get("kind") == "draw"),
            "quality": (c.get("frame") or {}).get("quality")}


def _side(c, core):
    """{variant: aggregate} and {stage: cycles} of one run on one core."""
    variants, stages, seen = {}, Counter(), Counter()
    for e in c.get("events") or []:
        x = (e.get("cost") or {}).get(core)
        if not x:
            continue
        vk = e.get("variant") or e.get("shader") or f"#{e['index']}"
        v = variants.get(vk)
        if v is None:
            v = variants[vk] = {"variant": vk, "shader": e.get("shader") or vk, "pass": e.get("pass"),
                                "keywords": e.get("keywords") or [], "kind": e.get("kind"), "events": [],
                                "work": Counter(), "cost": Counter(), "total": 0.0, "flags": set(),
                                "stats": {k: x.get(k) for k in ("px_price", "vtx_price", "cs_price", "px_regs",
                                                                 "vtx_regs", "px_fp16", "px_bound", "vtx_bound",
                                                                 "cs_bound", "px_path")}}
        for stage, work, _ in STAGES:
            v["work"][stage] += e.get(work) or 0
            v["cost"][stage] += x.get(stage) or 0.0
        v["total"] += x.get("total") or 0.0
        v["flags"] |= set(x.get("flags") or [])
        key = (e.get("object"), e.get("stage"), e.get("rt"))
        seen[(vk,) + key] += 1
        v["events"].append({"key": key + (seen[(vk,) + key],), "index": e["index"], "object": e.get("object"),
                            "stage": e.get("stage"), "rt": e.get("rt"), "pixels": e.get("pixels") or 0,
                            "vertices": e.get("vertices") or 0, "threads": e.get("threads") or 0,
                            "total": x.get("total") or 0.0})
        stages[e.get("stage") or "other"] += x.get("total") or 0.0
    return variants, stages


def _price(v, stage, field):
    w = v["work"][stage]
    return v["cost"][stage] / w if w else (v["stats"].get(field) or 0.0)


def _events(va, vb):
    """Events of a variant matched by object, stage, render target and occurrence; largest change first."""
    a = {e["key"]: e for e in (va or {}).get("events", [])}
    b = {e["key"]: e for e in (vb or {}).get("events", [])}
    rows = []
    for k in list(dict.fromkeys(list(a) + list(b))):
        ea, eb = a.get(k), b.get(k)
        e = eb or ea
        rows.append({"object": e["object"], "stage": e["stage"], "rt": e["rt"],
                     "index_a": ea and ea["index"], "index_b": eb and eb["index"],
                     "a": ea["total"] if ea else None, "b": eb["total"] if eb else None,
                     "pixels_a": ea and ea["pixels"], "pixels_b": eb and eb["pixels"],
                     "vertices_a": ea and ea["vertices"], "vertices_b": eb and eb["vertices"],
                     "delta": (eb["total"] if eb else 0.0) - (ea["total"] if ea else 0.0)})
    rows.sort(key=lambda r: -abs(r["delta"]))
    return rows[:EVENTS_SHOWN], len(rows)


def _variant_row(va, vb):
    v = vb or va
    row = {"variant": v["variant"], "shader": v["shader"], "pass": v["pass"], "keywords": v["keywords"],
           "kind": v["kind"], "status": "both" if va and vb else "new" if vb else "gone",
           "a": va["total"] if va else 0.0, "b": vb["total"] if vb else 0.0, "price": 0.0, "work": 0.0, "mix": 0.0,
           "stages": {}}
    row["delta"] = row["b"] - row["a"]
    if va and vb:
        for stage, _, field in STAGES:
            wa, wb = va["work"][stage], vb["work"][stage]
            if not (wa or wb or va["cost"][stage] or vb["cost"][stage]):
                continue
            pa, pb = _price(va, stage, field), _price(vb, stage, field)
            price, work = (pb - pa) * (wa + wb) / 2, (wb - wa) * (pa + pb) / 2
            row["price"] += price
            row["work"] += work
            row["stages"][stage] = {"work_a": wa, "work_b": wb, "price_a": pa, "price_b": pb,
                                    "a": va["cost"][stage], "b": vb["cost"][stage], "price": price, "work": work}
        # cycles that are in the total but in no stage (none today) keep the identity exact
        row["price"] += row["delta"] - row["price"] - row["work"]
    else:
        row["mix"] = row["delta"]
        for stage, _, field in STAGES:
            if v["work"][stage] or v["cost"][stage]:
                row["stages"][stage] = {("work_b" if vb else "work_a"): v["work"][stage],
                                        ("price_b" if vb else "price_a"): _price(v, stage, field),
                                        ("b" if vb else "a"): v["cost"][stage]}
    row["stats_a"] = va and {**va["stats"], "flags": sorted(va["flags"])}
    row["stats_b"] = vb and {**vb["stats"], "flags": sorted(vb["flags"])}
    fa, fb = (va or {}).get("flags", set()), (vb or {}).get("flags", set())
    row["flags_added"], row["flags_removed"] = (sorted(fb - fa), sorted(fa - fb)) if va and vb else ([], [])
    row["events"], row["events_count"] = _events(va, vb)
    return row


def compare_core(a, b, core):
    va, sa = _side(a, core)
    vb, sb = _side(b, core)
    rows = [_variant_row(va.get(k), vb.get(k)) for k in dict.fromkeys(list(va) + list(vb))]
    shaders = {}
    for r in rows:
        s = shaders.setdefault(r["shader"], {"shader": r["shader"], "a": 0.0, "b": 0.0, "price": 0.0, "work": 0.0,
                                             "mix": 0.0, "variants": [], "work_a": Counter(), "work_b": Counter()})
        for k in ("a", "b", "price", "work", "mix"):
            s[k] += r[k]
        s["variants"].append(r)
        for stage, st in r["stages"].items():
            s["work_a"][stage] += st.get("work_a") or 0
            s["work_b"][stage] += st.get("work_b") or 0
    total_a, total_b = sum(v["total"] for v in va.values()), sum(v["total"] for v in vb.values())
    for s in shaders.values():
        s["delta"] = s["b"] - s["a"]
        st = {r["status"] for r in s["variants"]}
        s["status"] = "new" if st == {"new"} else "gone" if st == {"gone"} else "mixed" if st - {"both"} else "both"
        s["variants"].sort(key=lambda r: -abs(r["delta"]))
        s["work_a"], s["work_b"] = dict(s["work_a"]), dict(s["work_b"])
        s["flags_added"] = sorted({f for r in s["variants"] for f in r["flags_added"]})
        s["flags_removed"] = sorted({f for r in s["variants"] for f in r["flags_removed"]})
    stage_rows = [{"stage": k, "a": sa.get(k, 0.0), "b": sb.get(k, 0.0), "delta": sb.get(k, 0.0) - sa.get(k, 0.0)}
                  for k in sorted(set(sa) | set(sb), key=lambda k: -max(sa.get(k, 0.0), sb.get(k, 0.0)))]
    split = {k: {"a": sum(v["cost"][k] for v in va.values()), "b": sum(v["cost"][k] for v in vb.values())}
             for k, _, _ in STAGES}
    return {"core": core, "a": total_a, "b": total_b, "delta": total_b - total_a,
            "price": sum(r["price"] for r in rows), "work": sum(r["work"] for r in rows),
            "mix": sum(r["mix"] for r in rows), "split": split, "stages": stage_rows,
            "shaders": sorted(shaders.values(), key=lambda s: -abs(s["delta"]))}


def compare(a, b):
    """Comparison of runs a (before) and b (after) on every core both have."""
    ia, ib = info(a), info(b)
    cores = [c for c in ib["cores"] if c in ia["cores"]]
    main = ib["main_core"] if ib["main_core"] in cores else ia["main_core"] if ia["main_core"] in cores else (
        cores[0] if cores else None)
    same = bool(ia["frame_dir"]) and os.path.normcase(os.path.abspath(ia["frame_dir"])) == \
        os.path.normcase(os.path.abspath(ib["frame_dir"] or ""))
    warn = []
    if not cores:
        warn.append("У расчётов нет общих ядер: сравнить нечего.")
    elif set(ia["cores"]) != set(ib["cores"]):
        warn.append(f"Разные наборы ядер: сравниваются только общие ({', '.join(cores)}).")
    if ia["api"] != ib["api"]:
        warn.append(f"Разный API цен: A {ia['api']}, B {ib['api']}. Цены Vulkan и GLES отличаются сами по себе.")
    if ia["malioc"] != ib["malioc"]:
        warn.append(f"Разные версии malioc (A {ia['malioc']}, B {ib['malioc']}): часть разницы даёт компилятор, "
                    "а не шейдеры.")
    if ia["loops_n"] != ib["loops_n"]:
        warn.append(f"Разное число итераций динамических циклов (A n = {ia['loops_n']}, B n = {ib['loops_n']}).")
    if ia["resolution"] != ib["resolution"]:
        fmt = lambda r: "×".join(map(str, r)) if r else "?"
        warn.append(f"Разное разрешение кадра (A {fmt(ia['resolution'])}, B {fmt(ib['resolution'])}): "
                    "пиксели несопоставимы.")
    if ia["quality"] != ib["quality"] and not same:
        warn.append(f"Разный уровень качества Unity (A {ia['quality']}, B {ib['quality']}).")
    if ia["project"] and ib["project"] and os.path.normcase(ia["project"]) != os.path.normcase(ib["project"]):
        warn.append("Расчёты из разных проектов.")
    if ia["unpriced_draws"] or ib["unpriced_draws"]:
        warn.append(f"Draw без цены: в A {ia['unpriced_draws']}, в B {ib['unpriced_draws']}. Их стоимость не входит "
                    "в сравнение.")
    for side, i in (("A", ia), ("B", ib)):
        if i["checked"] is False:
            warn.append(f"{side}: шейдеры не перекомпилировались перед расчётом, цены могут быть устаревшими.")
    return {"a": ia, "b": ib, "same_frame": same, "cores": cores, "main_core": main, "warnings": warn,
            "by_core": {c: compare_core(a, b, c) for c in cores}, "made_at": time.time()}


def brief(cmp, n=3):
    """Total change on the main core and the shaders that changed most (the run panel)."""
    x = cmp["by_core"].get(cmp["main_core"])
    if not x:
        return None
    return {"core": x["core"], "a": x["a"], "b": x["b"], "delta": x["delta"],
            "pct": x["delta"] / x["a"] if x["a"] else None, "price": x["price"], "work": x["work"], "mix": x["mix"],
            "shaders": [{"shader": s["shader"], "delta": s["delta"], "status": s["status"]}
                        for s in x["shaders"][:n] if abs(s["delta"]) > 0]}
