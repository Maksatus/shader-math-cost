"""Local web UI: `python -m shaderopt ui`.

One window (Edge in app mode, else the default browser) on http://127.0.0.1:<port>:
  /            app.html: tabs "function costs" (docs/, the site as on GitHub), "project test" (the commands with
               their progress), "reports" (the snapshots in shaderopt/out)
  /site/...    docs/
  /out/...     shaderopt/out: reports of the project's frames; served only here, they never reach git or the site
  /api/...     JSON
A run is a chain of steps (`python -m shaderopt <cmd>` or a bench script) run one by one in a subprocess with
SHADEROPT_PROGRESS=1: its `##progress` lines (shaderopt/progress.py) move the phases of PHASES, everything else is
the log. One run at a time; it goes on when the window is closed (the server holds it) and its log is kept in
shaderopt/out/_ui/jobs. How long every phase took in the last runs (history.json) estimates the time left of the
phases that have no count and of the ones still to come.
"""
import argparse
import http.server
import json
import mimetypes
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser

from shaderopt import cli
from shaderopt import progress

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DOCS = os.path.join(ROOT, "docs")
OUT = cli.OUT
REAL = cli.REAL
STATE = os.path.join(OUT, "_ui")
JOBS = os.path.join(STATE, "jobs")
LOG_LINES = 50000  # kept in memory per run; the file has all of them


def phases_of(cmd, v):
    """Phases a step goes through, in order (the ids progress.phase() prints); [] = one phase, "run"."""
    if cmd == "frame":
        rd = bool(v.get("renderdoc"))
        return (["rd_capture"] if rd else []) + ["snapshot"] + (["rd_counters"] if rd else [])
    if cmd == "cost":
        return (["materials"] if v.get("materials") else []) + ["fingerprints", "compile", "measure", "loops",
                                                                  "report"]
    if cmd == "export":
        return ["unity_export"] + (["measure"] if v.get("measure") else [])
    if cmd == "measure":
        return ["measure"]
    if cmd == "bench_run":
        return ["bench_compile"]
    return []


# a preset is what one button runs: its steps; `hide`: arguments the server fills in (the chain's own folders)
PRESETS = [
    {"id": "frame_cost", "steps": [{"cmd": "frame", "hide": ["out"]}, {"cmd": "cost", "hide": ["frame", "out", "project"]}]},
    {"id": "frame", "steps": [{"cmd": "frame"}]},
    {"id": "cost", "steps": [{"cmd": "cost"}]},
    {"id": "export", "steps": [{"cmd": "export"}]},
    {"id": "measure", "steps": [{"cmd": "measure"}]},
    {"id": "report", "steps": [{"cmd": "report"}]},
    {"id": "site", "steps": [{"cmd": "bench_run"}, {"cmd": "bench_site"}]},
]
BENCH = {  # bench scripts are not shaderopt commands: their arguments by hand
    "bench_run": {"help": "measure every function on every Mali GPU (bench/run.py) -> docs/mali_math_cost.csv",
                  "args": [{"dest": "gpus", "flag": "--gpus", "kind": "value", "default": "",
                            "help": "comma separated GPUs (default: all)"},
                           {"dest": "jobs", "flag": "--jobs", "kind": "value", "default": None,
                            "help": "parallel malioc runs (default: CPU count)"}]},
    "bench_site": {"help": "docs/data.js and docs/summary_*.csv from docs/mali_math_cost.csv (bench/build_site.py)",
                   "args": []},
}


def schema():
    """{command: {"help", "args": [...]}} from the argparse parser of shaderopt/cli.py, plus the bench scripts."""
    ap = cli.build_parser()
    sub = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help for a in sub._choices_actions}
    out = {}
    for name, p in sub.choices.items():
        if name == "ui":
            continue
        group = {id(a): i for i, g in enumerate(p._mutually_exclusive_groups) for a in g._group_actions}
        args = []
        for a in p._actions:
            if isinstance(a, argparse._HelpAction):
                continue
            if not a.option_strings:
                kind = "positional"
            elif isinstance(a, argparse._StoreTrueAction):
                kind = "flag"
            elif isinstance(a, argparse._AppendAction):
                kind = "list"
            else:
                kind = "value"
            default = a.default if isinstance(a.default, (str, int, float, bool)) or a.default is None else None
            args.append({"dest": a.dest, "flag": max(a.option_strings, key=len) if a.option_strings else None,
                         "kind": kind, "multi": a.nargs in ("+", "*"), "required": bool(a.required),
                         "default": default, "choices": list(a.choices) if a.choices else None,
                         "help": (a.help or "").replace("%(default)s", str(a.default)), "metavar": a.metavar,
                         "group": group.get(id(a))})
        out[name] = {"help": helps.get(name, ""), "args": args}
    out.update(BENCH)
    return out


def argv_of(cmd, values, sch):
    if cmd == "bench_run":
        argv = [sys.executable, "-u", os.path.join(ROOT, "bench", "run.py")]
    elif cmd == "bench_site":
        argv = [sys.executable, "-u", os.path.join(ROOT, "bench", "build_site.py")]
    else:
        argv = [sys.executable, "-u", "-m", "shaderopt", cmd]
    for a in sch[cmd]["args"]:
        v = values.get(a["dest"])
        if v in (None, "", [], False):
            continue
        items = v if isinstance(v, list) else [v]
        if a["kind"] == "flag":
            argv.append(a["flag"])
        elif a["kind"] == "positional":
            argv += [str(x) for x in items]
        elif a["kind"] == "list":
            for x in items:
                argv += [a["flag"], str(x)]
        else:
            argv += [a["flag"], str(v)]
    return argv


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


class Runner:
    """The current run and the ones before it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.job = None
        self.proc = None
        self.stopping = False
        self.history = _load(os.path.join(STATE, "history.json"), {})  # "cmd/phase" -> last durations, s
        self.past = []
        if os.path.isdir(JOBS):
            for fn in sorted(os.listdir(JOBS))[-30:]:
                j = _load(os.path.join(JOBS, fn), None)
                if j:
                    self.past.append(self.summary(j))

    # --- estimates -------------------------------------------------------------------------------------------
    def typical(self, cmd, pid):
        d = self.history.get(f"{cmd}/{pid}")
        return statistics.median(d) if d else None

    def remember(self, cmd, pid, seconds):
        d = self.history.setdefault(f"{cmd}/{pid}", [])
        d.append(round(seconds, 1))
        del d[:-5]

    def estimate(self, job, now):
        """Fills eta (seconds left) of every phase and step and of the whole run; None where nothing is known."""
        total, known = 0.0, True
        for s in job["steps"]:
            s_left, s_known = 0.0, True
            for p in s["phases"]:
                typ = self.typical(s["cmd"], p["id"])
                p["typical"] = typ
                left = None
                if p["status"] == "pending":
                    left = typ
                elif p["status"] == "running":
                    el = now - p["started"]
                    if p.get("total") and p.get("done"):
                        left = el / p["done"] * max(p["total"] - p["done"], 0) if el > 1.5 else typ
                    elif p.get("total") and typ:
                        left = typ
                    elif typ is not None:
                        left = max(typ - el, 0)
                    p["elapsed"] = el
                elif p["status"] in ("done", "skipped", "failed", "stopped"):
                    left = 0
                p["eta"] = left
                if left is None:
                    s_known = False
                else:
                    s_left += left
            if s["status"] == "pending" and not s["phases"]:
                s_known = False
            s["eta"] = s_left if s_known else None
            total += s_left
            known = known and s_known
        job["eta"] = total if job["status"] == "running" else 0
        job["eta_partial"] = not known

    # --- running ---------------------------------------------------------------------------------------------
    @staticmethod
    def summary(j):
        return {k: j.get(k) for k in ("id", "title", "preset", "status", "started", "finished", "report", "error")}

    def start(self, preset_id, values, sch):
        preset = next((p for p in PRESETS if p["id"] == preset_id), None)
        if not preset:
            raise ValueError(f"unknown preset {preset_id}")
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise ValueError("уже идёт запуск: дождитесь его конца или остановите")
        values = {k: v for k, v in values.items()}
        steps = []
        frame_dir = None
        if preset_id == "frame_cost":
            if not values.get("project"):
                raise ValueError("укажите Unity-проект")
            name = os.path.basename(os.path.abspath(values["project"]))
            frame_dir = os.path.join(OUT, f"frame_{name}_{time.strftime('%Y%m%d_%H%M%S')}")
        for st in preset["steps"]:
            cmd = st["cmd"]
            mine = {a["dest"] for a in sch[cmd]["args"]}
            v = {k: values[k] for k in mine if k in values and k not in st.get("hide", [])}
            if preset_id == "frame_cost":
                if cmd == "frame":
                    v["out"] = frame_dir
                else:
                    v["frame"] = frame_dir
                    v["project"] = values["project"]
            missing = [a["flag"] or a["dest"] for a in sch[cmd]["args"]
                       if (a["required"] or a["kind"] == "positional") and v.get(a["dest"]) in (None, "", [])]
            if missing:
                raise ValueError(f"{cmd}: не заполнено {', '.join(missing)}")
            steps.append({"cmd": cmd, "argv": argv_of(cmd, v, sch), "values": v, "status": "pending",
                          "started": None, "finished": None, "rc": None,
                          "phases": [{"id": p, "status": "pending"} for p in (phases_of(cmd, v) or ["run"])]})
        report = None
        for s in steps:
            if s["cmd"] == "cost":
                report = os.path.join(s["values"].get("out") or s["values"]["frame"], "frame_report.html")
        now = time.time()
        job = {"id": time.strftime("%Y%m%d_%H%M%S", time.localtime(now)), "preset": preset_id, "title": preset_id,
               "status": "running", "started": now, "finished": None, "steps": steps, "log": [], "log_start": 0,
               "report_path": report, "report": None, "error": None, "frame_dir": frame_dir}
        os.makedirs(JOBS, exist_ok=True)
        with self.lock:
            self.job = job
            self.stopping = False
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job["id"]

    def _log(self, job, line, f):
        f.write(line + "\n")
        f.flush()
        job["log"].append(line)
        if len(job["log"]) > LOG_LINES:
            drop = len(job["log"]) - LOG_LINES
            del job["log"][:drop]
            job["log_start"] += drop

    def _run(self, job):
        env = dict(os.environ, SHADEROPT_PROGRESS="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   PYTHONUNBUFFERED="1")
        with open(os.path.join(JOBS, job["id"] + ".log"), "w", encoding="utf-8", newline="\n") as f:
            try:
                self._steps(job, env, f)
            except Exception as e:  # a bug here must not leave the run hanging as "running"
                with self.lock:
                    self._log(job, traceback.format_exc().rstrip(), f)
                    job["error"] = f"ошибка UI: {e}"
                    for s in job["steps"]:
                        if s["status"] == "running":
                            self._end_step(s, "failed")
            with self.lock:
                for s in job["steps"]:
                    if s["status"] == "pending":
                        s["status"] = "cancelled"
                        for p in s["phases"]:
                            p["status"] = "cancelled"
                if self.stopping:
                    job["status"] = "stopped"
                elif job["error"] or any(s["status"] != "ok" for s in job["steps"]):
                    job["status"] = "failed"
                else:
                    job["status"] = "ok"
                job["finished"] = time.time()
                rp = job.get("report_path")
                if rp and os.path.exists(rp) and job["status"] == "ok":
                    job["report"] = out_url(rp) or f"/jobreport/{job['id']}"  # --out outside shaderopt/out
                self._log(job, f"--- {job['status']} за {fmt_time(job['finished'] - job['started'])}", f)
                _save(os.path.join(STATE, "history.json"), self.history)
                _save(os.path.join(JOBS, job["id"] + ".json"), job)
                self.past = [p for p in self.past if p["id"] != job["id"]][-29:] + [self.summary(job)]

    def _steps(self, job, env, f):
        for s in job["steps"]:
            with self.lock:
                if self.stopping:
                    return
                s["status"], s["started"] = "running", time.time()
                if s["phases"][0]["id"] == "run":
                    self._phase(s, "run")
            self._log(job, f"$ {subprocess.list2cmdline(s['argv'])}", f)
            try:
                proc = subprocess.Popen(s["argv"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                        encoding="utf-8", errors="replace", bufsize=1)
            except OSError as e:
                with self.lock:
                    self._log(job, f"не удалось запустить: {e}", f)
                    self._end_step(s, "failed")
                    job["error"] = str(e)
                return
            with self.lock:
                self.proc = proc
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                with self.lock:
                    if line.startswith(progress.PREFIX):
                        try:
                            self._event(s, json.loads(line[len(progress.PREFIX):]))
                        except ValueError:
                            self._log(job, line, f)
                    else:
                        self._log(job, line, f)
            rc = proc.wait()
            with self.lock:
                self.proc = None
                s["rc"] = rc
                if self.stopping:
                    self._end_step(s, "stopped")
                    return
                if rc != 0:
                    self._end_step(s, "failed")
                    job["error"] = f"{s['cmd']}: код выхода {rc}"
                    return
                self._end_step(s, "ok")

    def _phase(self, s, pid, total=None, note=None):
        now = time.time()
        for p in s["phases"]:
            if p["status"] == "running" and p["id"] != pid:
                self._finish(s, p, "done", now)
        p = next((p for p in s["phases"] if p["id"] == pid), None)
        if p is None:
            p = {"id": pid, "status": "pending"}
            s["phases"].append(p)
        idx = s["phases"].index(p)
        for q in s["phases"][:idx]:
            if q["status"] == "pending":
                q["status"] = "skipped"
        if p["status"] != "running":
            p.update(status="running", started=now)
        p.update(total=total, done=0 if total else None, note=note)

    def _finish(self, s, p, status, now):
        p["status"], p["finished"] = status, now
        if status == "done" and p.get("started"):
            p["seconds"] = now - p["started"]
            self.remember(s["cmd"], p["id"], p["seconds"])

    def _event(self, s, e):
        ev = e.get("event")
        if ev == "phase":
            self._phase(s, e["id"], e.get("total"), e.get("note"))
        elif ev == "skip":
            p = next((p for p in s["phases"] if p["id"] == e["id"]), None)
            if p and p["status"] in ("pending", "running"):
                p.update(status="skipped", note=e.get("note"))
        elif ev == "step":
            p = next((p for p in s["phases"] if p["status"] == "running"), None)
            if p:
                if e.get("total") is not None:
                    p["total"] = e["total"]
                if e.get("done") is not None:
                    p["done"] = e["done"]
                if e.get("note"):
                    p["note"] = e["note"]

    def _end_step(self, s, status):
        now = time.time()
        s["status"], s["finished"] = status, now
        for p in s["phases"]:
            if p["status"] == "running":
                self._finish(s, p, "done" if status == "ok" else status, now)
            elif p["status"] == "pending":
                p["status"] = "skipped" if status == "ok" else "cancelled"

    def stop(self):
        with self.lock:
            if not self.job or self.job["status"] != "running":
                return False
            self.stopping = True
            proc = self.proc
        if proc and proc.poll() is None:
            # the whole tree: malioc, qrenderdoc, the Unity CLI client (the editor itself is not a child)
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        return True

    def view(self, job_id=None, since=0):
        with self.lock:
            job = self.job if job_id in (None, "", (self.job or {}).get("id")) else None
            if job is None and job_id:
                job = _load(os.path.join(JOBS, f"{os.path.basename(job_id)}.json"), None)
            if job is None:
                return None
            now = time.time()
            if job["status"] == "running":
                self.estimate(job, now)
            v = {k: val for k, val in job.items() if k != "log"}
            v["now"] = now
            start = max(since - job.get("log_start", 0), 0)
            v["log"] = job["log"][start:]
            v["log_next"] = job.get("log_start", 0) + len(job["log"])
            return json.loads(json.dumps(v))


def fmt_time(s):
    s = int(s)
    return f"{s // 60} мин {s % 60} с" if s >= 60 else f"{s} с"


def out_url(path):
    path, out = os.path.abspath(path), os.path.abspath(OUT)
    try:
        if os.path.commonpath([path, out]) != out:
            return None
    except ValueError:  # another drive
        return None
    return "/out/" + urllib.parse.quote(os.path.relpath(path, out).replace("\\", "/"))


# --- what the forms offer --------------------------------------------------------------------------------------
_cache = {}


def _cached_json(path):
    try:
        m = os.path.getmtime(path)
    except OSError:
        return None
    c = _cache.get(path)
    if c and c[0] == m:
        return c[1]
    d = _load(path, None)
    _cache[path] = (m, d)
    return d


def snapshots():
    rows = []
    if not os.path.isdir(OUT):
        return rows
    for name in os.listdir(OUT):
        d = os.path.join(OUT, name)
        ev = os.path.join(d, "frame_events.json")
        if not os.path.exists(ev):
            continue
        meta = _cached_json(ev) or {}
        events = meta.get("events") or []
        row = {"name": name, "path": d, "time": os.path.getmtime(ev), "project": meta.get("project"),
               "unity": meta.get("unity"), "api": meta.get("graphics_api"), "play_mode": meta.get("play_mode"),
               "events": len(events), "draws": sum(1 for e in events if e.get("kind") == "draw"),
               "renderdoc": bool(meta.get("renderdoc")), "report": None, "cost_time": None}
        rp = os.path.join(d, "frame_report.html")
        if os.path.exists(rp):
            row["report"] = out_url(rp)
            row["cost_time"] = os.path.getmtime(rp)
        cost = _cached_json(os.path.join(d, "frame_cost.json"))
        if cost:
            mc = cost.get("main_core")
            t = (cost.get("totals") or {}).get(mc) or {}
            cov = cost.get("coverage") or {}
            row.update(main_core=mc, total=t.get("total"), fragment=t.get("fragment"), vertex=t.get("vertex"),
                       compute=t.get("compute"), priced=cov.get("draws_priced"), api_cost=cost.get("api"))
        rows.append(row)
    return sorted(rows, key=lambda r: -r["time"])


def options():
    snaps = snapshots()
    settings = _load(os.path.join(STATE, "settings.json"), {})
    projects = list(dict.fromkeys(list(settings.get("projects", [])) + [s["project"] for s in snaps if s["project"]]))
    variants = [os.path.join(OUT, n) for n in sorted(os.listdir(OUT))
                if n.startswith("variants") and os.path.isdir(os.path.join(OUT, n))] if os.path.isdir(OUT) else []
    folders = variants + ([os.path.join(REAL, n) for n in sorted(os.listdir(REAL))] if os.path.isdir(REAL) else [])
    from shaderopt.profile import cores
    return {"projects": projects, "snapshots": [{"path": s["path"], "name": s["name"], "project": s["project"]}
                                                for s in snaps],
            "variants": variants, "folders": folders, "measured": [f for f in folders
                                                                    if os.path.exists(os.path.join(f, "measurements.jsonl"))],
            "cores": [f"preset:{k}" for k in cores.PRESETS] + sorted({c for v in cores.PRESETS.values() for c in v}),
            "last": settings.get("last", {}), "out": OUT}


def remember_values(preset, values):
    path = os.path.join(STATE, "settings.json")
    s = _load(path, {})
    s.setdefault("last", {})[preset] = values
    if values.get("project"):
        s["projects"] = list(dict.fromkeys([values["project"]] + s.get("projects", [])))[:10]
    _save(path, s)


def editor_status(project):
    from shaderopt.unity import export
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


# --- HTTP ------------------------------------------------------------------------------------------------------
RUNNER = None
SCHEMA = None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "shaderopt-ui"

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")  # DNS rebinding: other names are refused

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, base, rel):
        path = os.path.abspath(os.path.join(base, urllib.parse.unquote(rel)))
        if os.path.commonpath([path, os.path.abspath(base)]) != os.path.abspath(base):
            return self.send_error(403)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if not os.path.isfile(path):
            return self.send_error(404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self):
        if not self._host_ok():
            return self.send_error(403)
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._file(HERE, "app.html")
            if p.startswith("/site/"):
                return self._file(DOCS, p[len("/site/"):])
            if p.startswith("/out/"):
                return self._file(OUT, p[len("/out/"):])
            if p.startswith("/jobreport/"):
                j = RUNNER.view(p[len("/jobreport/"):])
                rp = (j or {}).get("report_path")
                if not rp or not os.path.isfile(rp):
                    return self.send_error(404)
                return self._file(os.path.dirname(rp), os.path.basename(rp))
            if p == "/api/ping":
                return self._json({"app": "shaderopt-ui"})
            if p == "/api/schema":
                return self._json({"commands": SCHEMA, "presets": PRESETS})
            if p == "/api/options":
                return self._json(options())
            if p == "/api/job":
                return self._json({"job": RUNNER.view(q.get("id"), int(q.get("since", 0)))})
            if p == "/api/jobs":
                with RUNNER.lock:
                    past = list(reversed(RUNNER.past))
                    cur = RUNNER.summary(RUNNER.job) if RUNNER.job else None
                return self._json({"jobs": past, "current": cur})
            if p == "/api/reports":
                return self._json({"reports": snapshots()})
            if p == "/api/editor":
                return self._json(editor_status(q.get("project", "")))
            return self.send_error(404)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_POST(self):
        # a custom header makes a cross-origin request preflighted, and no CORS is answered: other pages cannot post
        if not self._host_ok() or self.headers.get("X-Shaderopt") != "1":
            return self.send_error(403)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/api/run":
                values = body.get("values") or {}
                job_id = RUNNER.start(body.get("preset"), values, SCHEMA)
                remember_values(body.get("preset"), values)
                return self._json({"id": job_id})
            if p == "/api/stop":
                return self._json({"stopped": RUNNER.stop()})
            if p == "/api/reveal":
                path = snapshot_dir(body.get("name")) if body.get("name") else os.path.abspath(body.get("path", ""))
                if path and os.path.isdir(path):
                    os.startfile(path)
                    return self._json({"ok": True})
                return self._json({"error": "нет такой папки"}, 404)
            if p == "/api/delete":
                path = snapshot_dir(body.get("name"))
                if not path:
                    return self._json({"error": "нет такого снимка"}, 404)
                with RUNNER.lock:
                    busy = RUNNER.job and RUNNER.job["status"] == "running" and any(
                        isinstance(x, str) and os.path.abspath(x) == path
                        for s in RUNNER.job["steps"] for x in s["values"].values())
                if busy:
                    return self._json({"error": "снимок используется идущим запуском"}, 409)
                shutil.rmtree(path)
                return self._json({"ok": True})
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        return self.send_error(404)


def snapshot_dir(name):
    """A snapshot folder directly in shaderopt/out (nothing else is deleted or opened by name)."""
    if not name or os.path.basename(name) != name or name.startswith((".", "_")):
        return None
    path = os.path.join(OUT, name)
    return path if os.path.exists(os.path.join(path, "frame_events.json")) else None


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = False  # on Windows SO_REUSEADDR lets a second server bind the same port
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def launch_window(url):
    edge = shutil.which("msedge") or next(
        (p for p in (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                     os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"))
         if os.path.exists(p)), None)
    if edge:
        try:
            subprocess.Popen([edge, f"--app={url}", "--window-size=1500,950"])
            return
        except OSError:
            pass
    webbrowser.open(url)


def serve(port=8765, open_window=True):
    global RUNNER, SCHEMA
    url = f"http://127.0.0.1:{port}/"
    try:
        httpd = Server(("127.0.0.1", port), Handler)
    except OSError:
        try:  # already running: just open another window on it
            with urllib.request.urlopen(url + "api/ping", timeout=3) as r:
                if json.load(r).get("app") == "shaderopt-ui":
                    print(f"shaderopt ui is already running on {url}")
                    if open_window:
                        launch_window(url)
                    return 0
        except (OSError, ValueError):
            pass
        sys.exit(f"port {port} is busy: --port <another>")
    RUNNER = Runner()
    SCHEMA = schema()
    print(f"shaderopt ui on {url}  (Ctrl+C to stop; a running job is stopped too)")
    if open_window:
        launch_window(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        RUNNER.stop()
        httpd.server_close()
    return 0
