import json
import os
import re
import time
import urllib.parse

from paretogpu.app import code_version
from paretogpu.core import compare, frame_cost
from paretogpu.store import cost_runs as runs_store
from paretogpu.store import results as store_results
from paretogpu.store import workspace
from paretogpu.views import messages

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


PANEL = ("main_core", "api", "total", "fragment", "vertex", "compute", "coverage", "shaders", "unpriced_draws",
         "unpriced_compute", "failed_reasons", "checked", "pixel_methods", "renderdoc")


def cost_summary(folder):
    """Run panel data: PANEL keys of frame_cost.summary + prev (compare with the previous run)."""
    c = load_json(os.path.join(folder, workspace.COST_FILE), None)
    if not c:
        return None
    prev = None
    rs = runs_store.runs(folder)
    if len(rs) >= 2:
        try:
            cmp = compare.compare(runs_store.load(runs_store.run_path(folder, rs[-2]["id"])),
                                  runs_store.load(runs_store.run_path(folder, rs[-1]["id"])))
            name = os.path.basename(folder.rstrip("\\/"))
            prev = {**(compare.brief(cmp) or {}), "run_a": f"{name}/{rs[-2]['id']}", "run_b": f"{name}/{rs[-1]['id']}",
                    "a_time": rs[-2].get("computed_at"), "warnings": [messages.text(w) for w in cmp["warnings"]]}
        except (OSError, ValueError):
            prev = None
    s = frame_cost.summary(c)
    warnings = [messages.text(w) for w in (c.get("profile") or {}).get("warnings") or []]
    return {"prev": prev, "warnings": warnings, **{k: s[k] for k in PANEL}}


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
               "renderdoc": bool(meta.get("renderdoc")), "phone": (meta.get("device") or {}).get("model"),
               "cost_time": None, "pages": {}, "thumb": None}
        thumb = os.path.join(d, workspace.THUMB)
        if os.path.exists(thumb):
            row["thumb"] = f"{out_url(thumb)}?v={int(os.path.getmtime(thumb))}"
        rs = runs_store.runs(d)
        row["runs"] = [m["id"] for m in rs]
        for m in store_results.of_folder(d):
            page = os.path.join(d, m["page"]) if m.get("page") else None
            if page and os.path.exists(page):
                row["pages"][m["kind"]] = out_url(page)
                if m["kind"] == "cost":
                    row["cost_time"] = os.path.getmtime(page)
                    t = m.get("summary") or {}
                    row.update(main_core=t.get("main_core"), total=t.get("total"), fragment=t.get("fragment"),
                               vertex=t.get("vertex"), compute=t.get("compute"), priced=t.get("priced"),
                               api_cost=t.get("api"), stale=t.get("code_version") != code_version.current())
        rows.append(row)
    return sorted(rows, key=lambda r: -r["time"])


RUN_ID = re.compile(r"\d{8}_\d{6}b*")


def cost_runs():
    rows = []
    for snap in snapshots():
        for m in runs_store.runs(snap["path"]):
            rows.append({"id": f"{snap['name']}/{m['id']}", "snapshot": snap["name"], "project": snap["project"],
                         "snapshot_time": snap["time"], "computed_at": m.get("computed_at"), "api": m.get("api"),
                         "main_core": m.get("main_core"), "total": (m.get("totals") or {}).get(m.get("main_core")),
                         "cores": m.get("cores"), "malioc": m.get("malioc"), "checked": m.get("checked")})
    return sorted(rows, key=lambda r: -(r["computed_at"] or 0))


def run_file(rid):
    """"<snapshot>/<run>" -> run file path; validated so an id can only reach a cost run in paretogpu/out."""
    name, _, run = (rid or "").partition("/")
    folder = workspace.snapshot_dir(name)
    if not folder or not RUN_ID.fullmatch(run):
        return None
    path = runs_store.run_path(folder, run)
    return path if os.path.isfile(path) else None


def results(kind=None):
    rows = []
    for m in store_results.scan(kind):
        page = os.path.join(m["folder"], m["page"]) if m.get("page") else None
        rows.append({**m, "url": out_url(page) if page and os.path.exists(page) else None})
    return rows


_materials = {}  # project -> (time, rows)


def project_materials(project, fresh=False):
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
    """Paths of materials named `name`, same content first: a .mat dropped from Explorer has no path.
    A miss rescans (the cached list may predate a branch switch)."""
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
    d = load_json(os.path.join(os.environ.get("APPDATA", ""), "UnityHub", "projects-v1.json"), {})
    rows = [v for v in (d.get("data") or {}).values() if isinstance(v, dict) and v.get("path")]
    return sorted(rows, key=lambda v: -(v.get("lastModified") or 0))


_phones = {"time": 0, "rows": []}


def phones(unity):
    """Phones with a project's build installed, as pseudo-projects with path "device:<serial>:<project>"."""
    from paretogpu.adapters import renderdoc as rdoc
    from paretogpu.adapters.unity import cli as export
    if time.time() - _phones["time"] < 15:
        return _phones["rows"]
    rows = []
    for serial, model in rdoc.devices():
        installed = rdoc.installed_packages(serial)
        for p in unity:
            pkg = export.android_package(p["path"])
            if pkg and pkg in installed and not pkg.startswith(("com.UnityTechnologies.", "com.DefaultCompany.")):
                rows.append({"path": f"device:{serial}:{p['path']}", "title": f"Телефон {model} · {p['title']}",
                             "serial": serial, "model": model, "package": pkg, "project": p["path"],
                             "version": None, "open": True, "device": True, "snapshots": 0})
    _phones.update(time=time.time(), rows=rows)
    return rows


def parse_phone(value):
    if not (value or "").startswith("device:"):
        return None
    serial, _, project = value[len("device:"):].partition(":")
    return serial, project


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
    from paretogpu.model import cores
    unity += phones([p for p in unity])
    return {"projects": projects, "unity_projects": unity, "snapshots": [{"path": s["path"], "name": s["name"], "project": s["project"]}
                                                for s in snaps],
            "variants": variants,
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
    phone = parse_phone(project)
    if phone:
        from paretogpu.adapters import renderdoc as rdoc
        serial, proj = phone
        st = {"project": True, "device": True, "cli": True, "open": True}
        connected = serial in [s for s, _ in rdoc.devices()]
        pkg = export.android_package(proj)
        st["installed"] = bool(connected and pkg and pkg in rdoc.installed_packages(serial))
        st["unlocked"] = bool(connected and rdoc.device_unlocked(serial))
        st["connected"] = connected
        st["ready"] = connected and st["installed"] and st["unlocked"]
        return st
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
