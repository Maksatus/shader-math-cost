"""Two cost runs compared per core, events matched by shader variant (indices move between frames).
Per variant and stage (w = pixels / vertices / threads, p = cycles / w, r = reference price with every loop once):
  price = (rB - rA) * (wA + wB) / 2           code change at fixed trip counts
  loops = (pB - pA) * (wA + wB) / 2 - price   dynamic loop trip counts changed
  (trip counts equal on both sides: price = (pB - pA) * (wA + wB) / 2, loops = 0; r would misplace an edit of a loop
  body run n != 1 times)
  work  = (wB - wA) * (pA + pB) / 2           work changed (scene, camera, LOD)
price + loops + work = B - A exactly; a variant on one side only is `mix`. Warning texts: views/messages.py.
"""
import os
import time
from collections import Counter

from paretogpu.core import frame_cost
from paretogpu.core import profile
from paretogpu.model.cost import CostRun

STAGES = (("fragment", "pixels", "px_price"), ("vertex", "vertices", "vtx_price"), ("compute", "threads", "cs_price"))
REF = {"px_price": "px_ref", "vtx_price": "vtx_ref", "cs_price": "cs_ref"}
EVENTS_SHOWN = 12


INFO =("snapshot", "frame_dir", "project", "computed_at", "code_version", "malioc", "main_core", "cores",
        "resolution", "checked", "unpriced_draws", "quality", "pipeline", "scale", "render",
        "pixel_methods", "vertex_methods")


def _setting(v):
    if isinstance(v, list) and v and isinstance(v[0], list):
        return ", ".join("×".join(map(str, x)) for x in v)
    if isinstance(v, list) and v and isinstance(v[0], int):
        return "×".join(map(str, v))
    if isinstance(v, list):
        return ", ".join(v) or "—"
    return "?" if v is None else str(v)


def info(c):
    s =frame_cost.summary(c)
    return {k: s[k] for k in INFO}


def _side(c, core):
    """-> ({variant: aggregate}, {stage: cycles}, variant keys with no event priced on core)."""
    variants, stages, seen, unpriced = {}, Counter(), Counter(), set()
    for e in c.get("events") or []:
        x = (e.get("cost") or {}).get(core)
        vk = e.get("variant") or e.get("shader") or f"#{e['index']}"
        if not x:
            unpriced.add(vk)
            continue
        v = variants.get(vk)
        if v is None:
            v = variants[vk] = {"variant": vk, "shader": e.get("shader") or vk, "pass": e.get("pass"),
                                "keywords": e.get("keywords") or [], "kind": e.get("kind"), "events": [],
                                "work": Counter(), "cost": Counter(), "ref": Counter(), "total": 0.0,
                                "flags": set(), "trips": Counter(),
                                "stats": {k: x.get(k) for k in ("px_price", "vtx_price", "cs_price", "px_regs",
                                                                 "vtx_regs", "px_fp16", "px_bound", "vtx_bound",
                                                                 "cs_bound", "px_path", "px_ref", "vtx_ref",
                                                                 "cs_ref")}}
        for stage, work, field in STAGES:
            ref = x.get(REF[field])
            if ref is None:
                ref = x.get(field) or 0.0
            v["work"][stage] += e.get(work) or 0
            v["cost"][stage] += x.get(stage) or 0.0
            v["ref"][stage] += ref * (e.get(work) or 0)
        v["total"] += x.get("total") or 0.0
        v["flags"] |= set(x.get("flags") or [])
        v["trips"][_trips(e)] += 1
        key = (e.get("object"), e.get("stage"), e.get("rt"))
        seen[(vk,) + key] += 1
        v["events"].append({"key": key + (seen[(vk,) + key],), "index": e["index"], "object": e.get("object"),
                            "stage": e.get("stage"), "rt": e.get("rt"), "pixels": e.get("pixels") or 0,
                            "vertices": e.get("vertices") or 0, "threads": e.get("threads") or 0,
                            "total": x.get("total") or 0.0})
        stages[e.get("stage") or "other"] += x.get("total") or 0.0
    return variants, stages, unpriced - set(variants)


def _trips(e):
    lp = e.get("loop") or {}
    return tuple(x.get("n") for x in lp["loops"]) if lp.get("loops") else lp.get("n")


def _price(v, stage, field):
    w = v["work"][stage]
    return v["cost"][stage] / w if w else (v["stats"].get(field) or 0.0)


def _ref(v, stage, field):
    w = v["work"][stage]
    s = v["stats"]
    return v["ref"][stage] / w if w else (s.get(REF[field]) if s.get(REF[field]) is not None else s.get(field) or 0.0)


def _events(va, vb):
    """Events matched by (object, stage, rt, occurrence); -> (top rows by |delta|, row count)."""
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
           "a": va["total"] if va else 0.0, "b": vb["total"] if vb else 0.0, "price": 0.0, "loops": 0.0, "work": 0.0,
           "mix": 0.0, "stages": {}}
    row["delta"] = row["b"] - row["a"]
    if va and vb:
        same_trips = va["trips"] == vb["trips"] and None not in va["trips"]
        for stage, _, field in STAGES:
            wa, wb = va["work"][stage], vb["work"][stage]
            if not (wa or wb or va["cost"][stage] or vb["cost"][stage]):
                continue
            pa, pb = _price(va, stage, field), _price(vb, stage, field)
            ra, rb = (pa, pb) if same_trips else (_ref(va, stage, field), _ref(vb, stage, field))
            price, work = (rb - ra) * (wa + wb) / 2, (wb - wa) * (pa + pb) / 2
            looped = (pb - pa) * (wa + wb) / 2 - price
            row["price"] += price
            row["loops"] += looped
            row["work"] += work
            row["stages"][stage] = {"work_a": wa, "work_b": wb, "price_a": pa, "price_b": pb, "ref_a": ra, "ref_b": rb,
                                    "a": va["cost"][stage], "b": vb["cost"][stage], "price": price, "loops": looped,
                                    "work": work}
        # total cycles outside every stage go to price so the identity stays exact
        row["price"] += row["delta"] - row["price"] - row["loops"] - row["work"]
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
    va, sa, unpriced_a = _side(a, core)
    vb, sb, unpriced_b = _side(b, core)
    rows = [_variant_row(va.get(k), vb.get(k)) for k in dict.fromkeys(list(va) + list(vb))]
    for r in rows:
        if (r["status"], r["variant"]) in {("new", k) for k in unpriced_a} | {("gone", k) for k in unpriced_b}:
            r["unpriced"] = "A" if r["status"] == "new" else "B"
    shaders = {}
    for r in rows:
        s = shaders.setdefault(r["shader"], {"shader": r["shader"], "a": 0.0, "b": 0.0, "price": 0.0, "loops": 0.0,
                                             "work": 0.0, "mix": 0.0, "variants": [], "work_a": Counter(),
                                             "work_b": Counter()})
        for k in ("a", "b", "price", "loops", "work", "mix"):
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
        if s["status"] != "both" and all(r.get("unpriced") for r in s["variants"] if r["status"] != "both"):
            s["status"] = "unpriced"
        s["variants"].sort(key=lambda r: -abs(r["delta"]))
        s["work_a"], s["work_b"] = dict(s["work_a"]), dict(s["work_b"])
        s["flags_added"] = sorted({f for r in s["variants"] for f in r["flags_added"]})
        s["flags_removed"] = sorted({f for r in s["variants"] for f in r["flags_removed"]})
    stage_rows = [{"stage": k, "a": sa.get(k, 0.0), "b": sb.get(k, 0.0), "delta": sb.get(k, 0.0) - sa.get(k, 0.0)}
                  for k in sorted(set(sa) | set(sb), key=lambda k: -max(sa.get(k, 0.0), sb.get(k, 0.0)))]
    split = {k: {"a": sum(v["cost"][k] for v in va.values()), "b": sum(v["cost"][k] for v in vb.values())}
             for k, _, _ in STAGES}
    return {"core": core, "a": total_a, "b": total_b, "delta": total_b - total_a,
            "price": sum(r["price"] for r in rows), "loops": sum(r["loops"] for r in rows),
            "work": sum(r["work"] for r in rows), "mix": sum(r["mix"] for r in rows),
            "split": split, "stages": stage_rows,
            "shaders": sorted(shaders.values(), key=lambda s: -abs(s["delta"]))}


def compare(a: CostRun, b: CostRun) -> dict:
    ia,ib = info(a), info(b)
    cores = [c for c in ib["cores"] if c in ia["cores"]]
    main = ib["main_core"] if ib["main_core"] in cores else ia["main_core"] if ia["main_core"] in cores else (
        cores[0] if cores else None)
    same = bool(ia["frame_dir"]) and os.path.normcase(os.path.abspath(ia["frame_dir"])) == \
        os.path.normcase(os.path.abspath(ib["frame_dir"] or ""))
    warn = []
    ab = lambda code, key, f=lambda x: x: warn.append({"code": code, "a": f(ia[key]), "b": f(ib[key])})
    if not cores:
        warn.append({"code": "no_common_cores"})
    elif set(ia["cores"]) != set(ib["cores"]):
        warn.append({"code": "cores_differ", "cores": ", ".join(cores)})
    if ia["code_version"] != ib["code_version"]:
        ab("code_differs", "code_version", lambda v: v or "?")
    if ia["malioc"] != ib["malioc"]:
        ab("malioc_differs", "malioc")
    for d in profile.differences(ia["render"], ib["render"]) if ia["render"] and ib["render"] else []:
        if d["what"] != "size":
            warn.append({"code": f"render_{d['what']}", "a": _setting(d["a"]), "b": _setting(d["b"])})
    for what in ("pixel", "vertex"):
        ma, mb = set(ia[f"{what}_methods"] or {}), set(ib[f"{what}_methods"] or {})
        if ma and mb and ma != mb:
            warn.append({"code": f"{what}_methods_differ", "a": ", ".join(sorted(ma)), "b": ", ".join(sorted(mb))})
    if ia["resolution"] != ib["resolution"]:
        ab("resolution_differs", "resolution", lambda r: "×".join(map(str, r)) if r else "?")
    if ia["quality"] and ib["quality"] and ia["quality"] != ib["quality"] and not same:  # a device frame has none
        ab("quality_differs", "quality")
    if ia["pipeline"] and ib["pipeline"] and ia["pipeline"] != ib["pipeline"] and not same:
        ab("pipeline_differs", "pipeline")
    if ia["scale"] and ib["scale"] and ia["scale"] != ib["scale"] and not same:
        ab("scale_differs", "scale", lambda x: f"{x:g}")
    if ia["project"] and ib["project"] and os.path.normcase(ia["project"]) != os.path.normcase(ib["project"]):
        warn.append({"code": "projects_differ"})
    if ia["unpriced_draws"] or ib["unpriced_draws"]:
        ab("unpriced_draws", "unpriced_draws")
    for side, i in (("A", ia), ("B", ib)):
        if i["checked"] is False:
            warn.append({"code": "unchecked", "side": side})
    ma, mb = _measured(a), _measured(b)
    if bool(ma) != bool(mb):
        return {"a": ia, "b": ib, "same_frame": False, "cores": cores, "main_core": main, "warnings": [],
                "by_core": {}, "model_vs_phone": dict(model_vs_phone(a, b) if mb else model_vs_phone(b, a),
                                                      phone="b" if mb else "a"), "made_at": time.time()}
    by_core = {c: compare_core(a, b, c) for c in cores}
    return {"a": ia, "b": ib, "same_frame": same, "cores": cores, "main_core": main, "warnings": warn,
            "by_core": by_core, "made_at": time.time()}


def _measured(c):
    sc = (c.get("hardware") or {}).get("shader_cost")
    return sc if sc and not sc.get("error") else None


def model_vs_phone(pc, phone):
    """The PC snapshot's model against the phone's measured shader cost (core/device.shader_cost), per shader and draw
    stage on the phone's core: "colour" rows against the model's fragment cycles, whole-draw rows (one shader name only)
    against the model's total; a row's phone cycles = its unit deltas summed, as the model sums pipes. Frame: both
    snapshots' totals by the same rules (shadows and depth apart, in "geometry").
    -> {"core", "frame": {"model", "phone", "model_geometry", "phone_geometry"}, "rows"} or {"core", "error"}."""
    sc, core = _measured(phone), phone["hardware"]["core"]
    if core not in [x["name"] for x in pc.get("cores") or []]:
        return {"core": core, "error": "core_missing"}
    model = Counter()
    for r in (pc.get("events") or []) + (pc.get("geometry") or []):
        x = (r.get("cost") or {}).get(core)
        if x:
            model[("colour", r.get("shader"), r["stage"])] += x.get("fragment") or 0.0
            model[("draw", r.get("shader"), r["stage"])] += x.get("total") or 0.0
    rows = {}
    for s in sc["shaders"]:
        if s.get("error") or s["mode"] not in ("colour", "draw") or (s["mode"] == "draw" and len(s["names"]) != 1):
            continue
        k = (s["mode"], s["shader"], s.get("draw_stage"))
        have = [(k[0], k[1], st) for st in (k[2] or "").split("+") if (k[0], k[1], st) in model]
        r = rows.setdefault(k, {"mode": k[0], "shader": k[1], "stage": k[2],
                                "model": sum(model[x] for x in have) if have else None, "exec": 0.0,
                                "noise": 0.0, "cycles": 0.0})
        r["exec"] += s["exec"]
        r["noise"] += s["noise"]
        r["cycles"] += frame_cost.knockout_cycles(s)
    rows = list(rows.values())

    def total(c, part):
        if part == "events":
            return ((c.get("totals") or {}).get(core) or {}).get("total")
        return sum(((r.get("cost") or {}).get(core) or {}).get("total") or 0.0 for r in c.get(part) or [])
    return {"core": core, "error": None, "rows": rows,
            "frame": {"model": total(pc, "events"), "phone": total(phone, "events"),
                      "model_geometry": total(pc, "geometry"), "phone_geometry": total(phone, "geometry")}}


def brief(cmp, n=3):
    x =cmp["by_core"].get(cmp["main_core"])
    if not x:
        return None
    return {"core": x["core"], "a": x["a"], "b": x["b"], "delta": x["delta"],
            "pct": x["delta"] / x["a"] if x["a"] else None, "price": x["price"], "loops": x["loops"],
            "work": x["work"], "mix": x["mix"],
            "shaders": [{"shader": s["shader"], "delta": s["delta"], "status": s["status"]}
                        for s in x["shaders"][:n] if abs(s["delta"]) > 0]}
