"""What the UI's pages read: the snapshots and their cost runs, the analyses of materials, the projects and their
materials, the environment checks; the summary of a finished cost run for the run panel.
"""
import json
import os
import re
import time
import urllib.parse

from paretogpu.core import compare
from paretogpu.store import cost_runs as runs_store
from paretogpu.store import workspace

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DOCS = os.path.join(ROOT, "docs")
OUT = workspace.OUT
STATE = workspace.UI


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def out_url(path):
    path, out = os.path.abspath(path), os.path.abspath(OUT)
    try:
        if os.path.commonpath([path, out]) != out:
            return None
    except ValueError:  # another drive
        return None
    return "/out/" + urllib.parse.quote(os.path.relpath(path, out).replace("\\", "/"))


def cost_summary(folder):
    """What the run panel shows after a cost: the frame on the main core, the costliest shaders, what has no price."""
    c = load_json(os.path.join(folder, "frame_cost.json"), None)
    if not c:
        return None
    mc = c.get("main_core")
    t = (c.get("totals") or {}).get(mc) or {}
    groups = (c.get("groups") or {}).get(mc) or {}
    missing = c.get("missing") or []
    failed = [m for m in missing if m.get("kind") == "draw"]
    prev = None
    rs = runs_store.runs(folder)
    if len(rs) >= 2:
        try:
            cmp = compare.compare(runs_store.load(runs_store.run_path(folder, rs[-2]["id"])),
                                  runs_store.load(runs_store.run_path(folder, rs[-1]["id"])))
            name = os.path.basename(folder.rstrip("\\/"))
            prev = {**(compare.brief(cmp) or {}), "run_a": f"{name}/{rs[-2]['id']}", "run_b": f"{name}/{rs[-1]['id']}",
                    "a_time": rs[-2].get("computed_at"), "warnings": cmp["warnings"]}
        except (OSError, ValueError):
            prev = None
    return {"prev": prev, "main_core": mc, "api": c.get("api"), "total": t.get("total"), "fragment": t.get("fragment"),
            "vertex": t.get("vertex"), "compute": t.get("compute"), "coverage": c.get("coverage"),
            "shaders": [{"key": g.get("key"), "share": g.get("share"), "total": g.get("total")}
                        for g in (groups.get("shader") or [])[:5]],
            "unpriced_draws": len(failed), "unpriced_compute": len(missing) - len(failed),
            "failed_reasons": sorted({(m.get("reason") or "")[:200] for m in failed})[:5],
            "checked": c.get("variants_checked"), "pixel_methods": c.get("pixel_methods"),
            "renderdoc": bool((c.get("frame") or {}).get("renderdoc"))}


_cache = {}


def _cached_json(path):
    try:
        m = os.path.getmtime(path)
    except OSError:
        return None
    c = _cache.get(path)
    if c and c[0] == m:
        return c[1]
    d = load_json(path, None)
    _cache[path] = (m, d)
    return d


def snapshots():
    rows = []
    for name, d in workspace.snapshot_dirs():
        ev = os.path.join(d, workspace.SNAPSHOT_FILE)
        meta = _cached_json(ev) or {}
        events = meta.get("events") or []
        row = {"name": name, "path": d, "time": os.path.getmtime(ev), "project": meta.get("project"),
               "unity": meta.get("unity"), "api": meta.get("graphics_api"), "play_mode": meta.get("play_mode"),
               "events": len(events), "draws": sum(1 for e in events if e.get("kind") == "draw"),
               "renderdoc": bool(meta.get("renderdoc")), "report": None, "cost_time": None}
        rs = runs_store.runs(d)
        row["runs"] = [m["id"] for m in rs]
        rp = os.path.join(d, "frame_report.html")
        if os.path.exists(rp):
            row["report"] = out_url(rp)
            row["cost_time"] = os.path.getmtime(rp)
        hs = os.path.join(d, "hotspots.html")
        row["hotspots"] = out_url(hs) if os.path.exists(hs) else None
        cost = _cached_json(os.path.join(d, "frame_cost.json"))
        if cost:
            mc = cost.get("main_core")
            t = (cost.get("totals") or {}).get(mc) or {}
            cov = cost.get("coverage") or {}
            row.update(main_core=mc, total=t.get("total"), fragment=t.get("fragment"), vertex=t.get("vertex"),
                       compute=t.get("compute"), priced=cov.get("draws_priced"), api_cost=cost.get("api"))
        rows.append(row)
    return sorted(rows, key=lambda r: -r["time"])


RUN_ID = re.compile(r"\d{8}_\d{6}b*")


def cost_runs():
    """Every kept cost run of the snapshots in paretogpu/out, newest first: what the comparison picks from."""
    rows = []
    for snap in snapshots():
        for m in runs_store.runs(snap["path"]):
            rows.append({"id": f"{snap['name']}/{m['id']}", "snapshot": snap["name"], "project": snap["project"],
                         "snapshot_time": snap["time"], "computed_at": m.get("computed_at"), "api": m.get("api"),
                         "main_core": m.get("main_core"), "total": (m.get("totals") or {}).get(m.get("main_core")),
                         "cores": m.get("cores"), "malioc": m.get("malioc"), "checked": m.get("checked")})
    return sorted(rows, key=lambda r: -(r["computed_at"] or 0))


def run_file(rid):
    """<snapshot>/<run> -> the run's file in paretogpu/out (nothing else is read by id)."""
    name, _, run = (rid or "").partition("/")
    folder = workspace.snapshot_dir(name)
    if not folder or not RUN_ID.fullmatch(run):
        return None
    path = runs_store.run_path(folder, run)
    return path if os.path.isfile(path) else None


def _results(kind):
    """[(id, folder, result)] of the analyses of `kind` in paretogpu/out (workspace.RESULTS) with their page."""
    rows = []
    folder = workspace.results_dir(kind)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            d = os.path.join(folder, name)
            r = _cached_json(os.path.join(d, f"{kind}.json"))
            if r and os.path.exists(os.path.join(d, f"{kind}.html")):
                rows.append((name, d, r))
    return rows


def matcompares():
    """Comparisons of two materials in paretogpu/out/_matcompare, newest first."""
    rows = [{"id": name, "url": out_url(os.path.join(d, "matcompare.html")), "project": r.get("project"),
             "computed_at": r.get("computed_at"), "a": {k: r["a"].get(k) for k in ("material", "path", "shader")},
             "b": {k: r["b"].get(k) for k in ("material", "path", "shader")}}
            for name, d, r in _results("matcompare")]
    return sorted(rows, key=lambda r: -(r["computed_at"] or 0))


def matshaders():
    """Analyses of one material in paretogpu/out/_matshader, newest first."""
    rows = [{"id": name, "url": out_url(os.path.join(d, "matshader.html")), "project": r.get("project"),
             "computed_at": r.get("computed_at"), "m": {k: r["m"].get(k) for k in ("material", "path", "shader")}}
            for name, d, r in _results("matshader")]
    return sorted(rows, key=lambda r: -(r["computed_at"] or 0))


_materials = {}  # project -> (time, rows)


def project_materials(project, fresh=False):
    """Materials of a Unity project for the pickers: path, name, shader (scanned once a minute at most, unless fresh)."""
    from paretogpu.adapters.unity import assets as materials
    key = os.path.normcase(os.path.abspath(project))
    hit = _materials.get(key)
    if hit and not fresh and time.time() - hit[0] < 60:
        return hit[1]
    rows = [{"path": m["path"], "name": m.get("name") or os.path.basename(m["path"]), "shader": m.get("shader"),
             "error": m.get("error")} for m in materials.scan(project)]
    _materials[key] = (time.time(), rows)
    return rows


def material_by_content(project, name, text):
    """Materials of the project named `name` (a .mat dropped from Explorer has no path), the same content first.
    A name missing from the cached list is looked up in a fresh scan (a branch switch adds materials)."""
    norm = lambda t: t.replace("\r\n", "\n").strip()
    named = [m for m in project_materials(project) if os.path.basename(m["path"]).lower() == name.lower()]
    if not named:
        named = [m for m in project_materials(project, fresh=True)
                 if os.path.basename(m["path"]).lower() == name.lower()]
    same, other = [], []
    for m in named:
        try:
            with open(os.path.join(project, m["path"]), encoding="utf-8", errors="replace") as f:
                (same if norm(f.read()) == norm(text) else other).append(m["path"])
        except OSError:
            pass
    return same + other


def hub_projects():
    """Unity Hub's project list (path, title, version), most recently opened first."""
    d = load_json(os.path.join(os.environ.get("APPDATA", ""), "UnityHub", "projects-v1.json"), {})
    rows = [v for v in (d.get("data") or {}).values() if isinstance(v, dict) and v.get("path")]
    return sorted(rows, key=lambda v: -(v.get("lastModified") or 0))


def options():
    from paretogpu.adapters.unity import cli as export
    snaps = snapshots()
    settings = load_json(os.path.join(STATE, "settings.json"), {})
    hub = {os.path.normcase(os.path.abspath(v["path"])): v for v in hub_projects()}
    paths = list(dict.fromkeys(list(settings.get("projects", [])) + [v["path"] for v in hub.values()]
                               + [s["project"] for s in snaps if s["project"]]))
    unity = []
    for path in paths:
        if not os.path.exists(os.path.join(path, "ProjectSettings", "ProjectVersion.txt")):
            continue
        h = hub.get(os.path.normcase(os.path.abspath(path))) or {}
        try:
            ver = h.get("version") or export.editor_version(path)
        except Exception:
            ver = None
        unity.append({"path": path, "title": h.get("title") or os.path.basename(os.path.abspath(path)),
                      "version": ver, "open": export.project_open(path),
                      "snapshots": sum(1 for x in snaps if x["project"]
                                       and os.path.normcase(x["project"]) == os.path.normcase(path))})
    projects = [p["path"] for p in unity]
    variants = workspace.variants_dirs()
    folders = variants + workspace.export_dirs()
    from paretogpu.model import cores
    return {"projects": projects, "unity_projects": unity, "snapshots": [{"path": s["path"], "name": s["name"], "project": s["project"]}
                                                for s in snaps],
            "variants": variants, "folders": folders, "measured": [f for f in folders
                                                                    if os.path.exists(os.path.join(f, "measurements.jsonl"))],
            "cores": [f"preset:{k}" for k in cores.PRESETS] + sorted({c for v in cores.PRESETS.values() for c in v}),
            "last": settings.get("last", {}), "out": OUT}


def remember_values(preset, values):
    path = os.path.join(STATE, "settings.json")
    s = load_json(path, {})
    s.setdefault("last", {})[preset] = values
    if values.get("project"):
        s["projects"] = list(dict.fromkeys([values["project"]] + s.get("projects", [])))[:10]
    save_json(path, s)


def doctor_checks():
    """paretogpu/doctor.py checks as [{status, what, detail}] for the status bar of the window."""
    import contextlib
    import io
    from paretogpu import doctor
    doctor.results.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        for check in (doctor.check_malioc, doctor.check_renderdoc, doctor.check_unity):
            try:
                check()
            except Exception as e:
                doctor.report(doctor.WARN, check.__name__[len("check_"):], f"проверка упала: {e!r}")
    return [{"status": st, "what": what, "detail": detail} for st, what, detail in doctor.results]


def site_info():
    path = os.path.join(DOCS, "mali_math_cost.csv")
    return {"updated": os.path.getmtime(path) if os.path.exists(path) else None}


def editor_status(project):
    from paretogpu.adapters.unity import cli as export
    project = os.path.abspath(project)
    if not os.path.exists(os.path.join(project, "ProjectSettings", "ProjectVersion.txt")):
        return {"project": False}
    st = {"project": True, "cli": bool(export.unity_cli()), "open": export.project_open(project)}
    try:
        st["version"] = export.editor_version(project)
    except Exception:
        st["version"] = None
    st["ready"] = bool(st["cli"] and st["open"] and export.editor_ready(project, allow_play=True))
    return st
