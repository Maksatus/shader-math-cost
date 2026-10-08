"""Local web UI: `python -m paretogpu ui`.

One window (Edge in app mode, else the default browser) on http://127.0.0.1:<port>:
  /            app.html: tabs "function costs" (docs/, the site as on GitHub), "project test" (the commands with
               their progress), "reports" (the snapshots in paretogpu/out)
  /site/...    docs/
  /out/...     paretogpu/out: reports of the project's frames; served only here, they never reach git or the site
  /api/...     JSON
A run is a chain of steps (`python -m paretogpu <cmd>` or a bench script) run one by one in a subprocess with
PARETOGPU_PROGRESS=1: its `##progress` lines (paretogpu/progress.py) move the phases of PHASES, everything else is
the log. One run at a time; it goes on when the window is closed (the server holds it) and its log is kept in
paretogpu/out/_ui/jobs. How long every phase took in the last runs (history.json) estimates the time left of the
phases that have no count and of the ones still to come.
"""
import argparse
import http.server
import json
import mimetypes
import os
import re
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

from paretogpu import cli
from paretogpu import progress
from paretogpu.core import compare
from paretogpu.store import cost_runs as runs_store
from paretogpu.store import workspace
from paretogpu.views import html

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DOCS = os.path.join(ROOT, "docs")
OUT = workspace.OUT
STATE = workspace.UI
MATCMP = workspace.results_dir("matcompare")  # results of `matcompare` started from the UI
MATSH = workspace.results_dir("matshader")  # results of `matshader` started from the UI
JOBS = os.path.join(STATE, "jobs")
LOG_LINES = 50000  # kept in memory per run; the file has all of them


def phases_of(cmd, v):
    """Phases a step goes through, in order (the ids progress.phase() prints); [] = one phase, "run"."""
    if cmd == "frame":
        return ["rd_capture", "snapshot", "rd_counters"]
    if cmd == "cost":
        return (["materials"] if v.get("materials") else []) + ["fingerprints", "compile", "measure", "loops",
                                                                  "report"]
    if cmd == "export":
        return ["unity_export"] + (["measure"] if v.get("measure") else [])
    if cmd == "measure":
        return ["measure"]
    if cmd == "matcompare":
        return ["materials", "fingerprints", "compile", "measure", "loops", "report"]
    if cmd == "matshader":
        return ["materials", "fingerprints", "compile", "measure", "loops", "ablation", "report"]
    if cmd == "hotspots":
        return ["ablation", "report"]
    if cmd == "bench_run":
        return ["bench_compile"]
    return []


# a preset is what one button runs: its steps; `hide`: arguments the server fills in (the chain's own folders).
# export, measure and report stay in the console: the UI is for checking a frame without knowing the CLI
PRESETS = [
    {"id": "frame_cost", "steps": [{"cmd": "frame", "hide": ["out"]}, {"cmd": "cost", "hide": ["frame", "out", "project"]}]},
    {"id": "cost", "steps": [{"cmd": "cost"}]},
    {"id": "site", "steps": [{"cmd": "bench_run"}, {"cmd": "bench_site"}]},
    {"id": "matcompare", "steps": [{"cmd": "matcompare"}]},
    {"id": "matshader", "steps": [{"cmd": "matshader"}]},
    {"id": "hotspots", "steps": [{"cmd": "hotspots"}]},
]
BENCH = {  # bench scripts are not paretogpu commands: their arguments by hand
    "bench_run": {"help": "measure every function on every Mali GPU (bench/run.py) -> docs/mali_math_cost.csv",
                  "args": [{"dest": "gpus", "flag": "--gpus", "kind": "value", "default": "",
                            "help": "comma separated GPUs (default: all)"},
                           {"dest": "jobs", "flag": "--jobs", "kind": "value", "default": None,
                            "help": "parallel malioc runs (default: CPU count)"}]},
    "bench_site": {"help": "docs/data.js and docs/summary_*.csv from docs/mali_math_cost.csv (bench/build_site.py)",
                   "args": []},
}


def schema():
    """{command: {"help", "args": [...]}} from the argparse parser of paretogpu/cli.py, plus the bench scripts."""
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
        argv = [sys.executable, "-u", "-m", "paretogpu", cmd]
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
            frame_dir = workspace.new_snapshot_dir(values["project"], values.get("suffix"))
        if preset_id == "matcompare":
            values["out"] = workspace.new_result_dir("matcompare")
        if preset_id == "matshader":
            values["out"] = workspace.new_result_dir("matshader")
        if preset_id in ("frame_cost", "cost", "matcompare", "matshader") and not values.get("variants"):
            # one variants folder per project: a variant is compiled once and priced in every frame of the project
            project = values.get("project") or (_cached_json(os.path.join(values.get("frame") or "",
                                                                          "frame_events.json")) or {}).get("project")
            if project:
                values["variants"] = workspace.variants_dir(project)
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
            if s["cmd"] == "matcompare":
                report = os.path.join(s["values"]["out"], "matcompare.html")
            if s["cmd"] == "matshader":
                report = os.path.join(s["values"]["out"], "matshader.html")
            if s["cmd"] == "hotspots":
                report = os.path.join(s["values"].get("out") or s["values"]["frame"], "hotspots.html")
        now = time.time()
        job = {"id": time.strftime("%Y%m%d_%H%M%S", time.localtime(now)), "preset": preset_id, "title": preset_id,
               "status": "running", "started": now, "finished": None, "steps": steps, "log": [], "log_start": 0,
               "report_path": report, "report": None, "error": None, "frame_dir": frame_dir,
               "summary": None, "hint": None}
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
        env = dict(os.environ, PARETOGPU_PROGRESS="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
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
                    job["report"] = out_url(rp) or f"/jobreport/{job['id']}"  # --out outside paretogpu/out
                    job["summary"] = cost_summary(os.path.dirname(rp))
                if job["status"] == "failed":
                    job["hint"] = hint_of(job)
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


def cost_summary(folder):
    """What the run panel shows after a cost: the frame on the main core, the costliest shaders, what has no price."""
    c = _load(os.path.join(folder, "frame_cost.json"), None)
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


# known failures -> what to do, in words for someone who has not seen the CLI; `action`: a button the panel offers
HINTS = [
    ("No Pipeline instance found", "Unity CLI в редакторе отключился. Включите его в Unity заново "
     "(пакет com.unity.pipeline) и запустите ещё раз: ожидание не поможет.", None),
    ("does not answer Unity CLI or is busy", "Unity не отвечает: откройте проект в Unity и дождитесь конца "
     "компиляции и импорта (полоса прогресса внизу редактора), затем запустите ещё раз.", "no_compile"),
    ("is not the game frame", "RenderDoc несколько раз подряд записал только окно редактора, а не кадр игры. Сделайте вкладку "
     "Game видимой (не за другой вкладкой, окно Unity не свёрнуто) и запустите проверку ещё раз.", None),
    ("renderdoc", "Не получилось снять кадр через RenderDoc. В Unity на вкладке Game нажмите правой кнопкой "
     "мыши → Load RenderDoc и запустите проверку ещё раз.", None),
    ("malioc", "Не найден или упал malioc (Arm Performance Studio). Запустите start.bat: он покажет, чего не "
     "хватает.", None),
    ("no frame_events.json", "Снимок не найден или не дописан: снимите кадр заново.", None),
]


def hint_of(job):
    text = ("\n".join(job["log"][-400:]) + "\n" + (job.get("error") or "")).lower()
    for needle, hint, action in HINTS:
        if needle.lower() in text:
            return {"text": hint, "action": action if any(s["cmd"] == "cost" for s in job["steps"]) else None}
    return {"text": "Что-то пошло не так, подробности в логе ниже. Если непонятно, пришлите лог тому, "
                    "кто поддерживает ParetoGPU.", "action": None}


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


def matcompares():
    """Comparisons of two materials in paretogpu/out/_matcompare, newest first."""
    rows = []
    if os.path.isdir(MATCMP):
        for name in os.listdir(MATCMP):
            r = _cached_json(os.path.join(MATCMP, name, "matcompare.json"))
            if r and os.path.exists(os.path.join(MATCMP, name, "matcompare.html")):
                rows.append({"id": name, "url": out_url(os.path.join(MATCMP, name, "matcompare.html")),
                             "project": r.get("project"), "computed_at": r.get("computed_at"),
                             "a": {k: r["a"].get(k) for k in ("material", "path", "shader")},
                             "b": {k: r["b"].get(k) for k in ("material", "path", "shader")}})
    return sorted(rows, key=lambda r: -(r["computed_at"] or 0))


def matshaders():
    """Analyses of one material in paretogpu/out/_matshader, newest first."""
    rows = []
    if os.path.isdir(MATSH):
        for name in os.listdir(MATSH):
            r = _cached_json(os.path.join(MATSH, name, "matshader.json"))
            if r and os.path.exists(os.path.join(MATSH, name, "matshader.html")):
                rows.append({"id": name, "url": out_url(os.path.join(MATSH, name, "matshader.html")),
                             "project": r.get("project"), "computed_at": r.get("computed_at"),
                             "m": {k: r["m"].get(k) for k in ("material", "path", "shader")}})
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
    d = _load(os.path.join(os.environ.get("APPDATA", ""), "UnityHub", "projects-v1.json"), {})
    rows = [v for v in (d.get("data") or {}).values() if isinstance(v, dict) and v.get("path")]
    return sorted(rows, key=lambda v: -(v.get("lastModified") or 0))


def options():
    from paretogpu.adapters.unity import cli as export
    snaps = snapshots()
    settings = _load(os.path.join(STATE, "settings.json"), {})
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
    s = _load(path, {})
    s.setdefault("last", {})[preset] = values
    if values.get("project"):
        s["projects"] = list(dict.fromkeys([values["project"]] + s.get("projects", [])))[:10]
    _save(path, s)


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


# --- HTTP ------------------------------------------------------------------------------------------------------
RUNNER = None
SCHEMA = None
CLOSE_GRACE = 6  # s without open windows before the server stops: a reloaded page says "alive" again by then
SILENT_CLOSED = 180  # s without "alive" = the window is gone (a minimized window still says it once a minute)


class Windows:
    """Open UI pages: each says "alive" every 20 s and "bye" when it is closed (a page killed without "bye" is
    dropped after SILENT_CLOSED)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ids = {}
        self.seen = False

    def alive(self, cid):
        with self.lock:
            self.ids[cid] = time.time()
            self.seen = True

    def bye(self, cid):
        with self.lock:
            self.ids.pop(cid, None)

    def all_closed(self):
        with self.lock:
            now = time.time()
            self.ids = {k: t for k, t in self.ids.items() if now - t < SILENT_CLOSED}
            return self.seen and not self.ids


WINDOWS = Windows()


def stop_when_closed(httpd):
    """Stops the server once every window is closed, after the running job (if any) has finished."""
    closed_at, waiting = None, False
    while True:
        time.sleep(1)
        if not WINDOWS.all_closed():
            closed_at, waiting = None, False
            continue
        closed_at = closed_at or time.time()
        if time.time() - closed_at < CLOSE_GRACE:
            continue
        with RUNNER.lock:
            busy = bool(RUNNER.job and RUNNER.job["status"] == "running")
        if busy:
            if not waiting:
                print("Окно закрыто: сервер остановится, когда закончится текущий запуск")
                waiting = True
            continue
        print("Окно закрыто: сервер остановлен")
        httpd.shutdown()
        return


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "paretogpu-ui"

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
                return self._json({"app": "paretogpu-ui"})
            if p == "/api/alive":
                if q.get("id"):
                    WINDOWS.alive(q["id"])
                return self._json({"ok": True})
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
            if p == "/api/costs":
                return self._json({"runs": cost_runs()})
            if p == "/api/matcompares":
                return self._json({"items": matcompares()})
            if p == "/api/matshaders":
                return self._json({"items": matshaders()})
            if p == "/api/materials":
                if not os.path.isdir(os.path.join(q.get("project", ""), "Assets")):
                    return self._json({"error": "нет такого Unity-проекта", "materials": []}, 404)
                return self._json({"materials": project_materials(q["project"])})
            if p == "/compare":
                fa, fb = run_file(q.get("a")), run_file(q.get("b"))
                if not fa or not fb:
                    return self.send_error(404, "no such cost run")
                cmp = compare.compare(runs_store.load(fa), runs_store.load(fb))
                body = html.render_result(cmp, f"Сравнение {q['a']} → {q['b']}").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                if q.get("download"):
                    fn = f"compare_{q['a'].replace('/', '_')}__{q['b'].replace('/', '_')}.html"
                    self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
                self.end_headers()
                self.wfile.write(body)
                return
            if p == "/api/doctor":
                return self._json({"checks": doctor_checks(), "site": site_info()})
            if p == "/api/editor":
                return self._json(editor_status(q.get("project", "")))
            return self.send_error(404)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def do_POST(self):
        if self._host_ok() and urllib.parse.urlparse(self.path).path == "/api/bye":
            # sendBeacon from a closing page (no custom header possible); the id is random, so no other page can guess it
            n = int(self.headers.get("Content-Length") or 0)
            WINDOWS.bye(self.rfile.read(n).decode("utf-8", "replace").strip())
            return self._json({"ok": True})
        # a custom header makes a cross-origin request preflighted, and no CORS is answered: other pages cannot post
        if not self._host_ok() or self.headers.get("X-ParetoGPU") != "1":
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
            if p == "/api/material_match":
                project = body.get("project") or ""
                if not os.path.isdir(os.path.join(project, "Assets")):
                    return self._json({"error": "нет такого Unity-проекта"}, 404)
                return self._json({"paths": material_by_content(project, body.get("name") or "", body.get("text") or "")})
            if p == "/api/stop":
                return self._json({"stopped": RUNNER.stop()})
            if p == "/api/reveal":
                path = workspace.snapshot_dir(body.get("name")) if body.get("name") else os.path.abspath(body.get("path", ""))
                if path and os.path.isdir(path):
                    os.startfile(path)
                    return self._json({"ok": True})
                return self._json({"error": "нет такой папки"}, 404)
            if p == "/api/delete":
                path = workspace.snapshot_dir(body.get("name"))
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


def serve(port=8765, open_window=True, stay=False):
    global RUNNER, SCHEMA
    url = f"http://127.0.0.1:{port}/"
    try:
        httpd = Server(("127.0.0.1", port), Handler)
    except OSError:
        try:  # already running: just open another window on it
            with urllib.request.urlopen(url + "api/ping", timeout=3) as r:
                if json.load(r).get("app") == "paretogpu-ui":
                    print(f"ParetoGPU ui is already running on {url}")
                    if open_window:
                        launch_window(url)
                    return 0
        except (OSError, ValueError):
            pass
        sys.exit(f"port {port} is busy: --port <another>")
    RUNNER = Runner()
    SCHEMA = schema()
    auto = open_window and not stay
    print(f"ParetoGPU ui on {url}  (Ctrl+C to stop; a running job is stopped too"
          + ("; closing the window stops it too)" if auto else ")"))
    if open_window:
        launch_window(url)
    if auto:
        threading.Thread(target=stop_when_closed, args=(httpd,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        RUNNER.stop()
        httpd.server_close()
    return 0
