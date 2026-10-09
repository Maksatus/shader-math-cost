"""The runs the UI starts: one at a time, a chain of steps run one by one in a subprocess with PARETOGPU_PROGRESS=1.

Its `##progress` lines (views/reporter.py) move the phases of the steps (ui/presets.py), everything else is the log.
A run goes on when the window is closed (the server holds it) and its log is kept in paretogpu/out/_ui/jobs. How long
every phase took in the last runs (history.json) estimates the time left of the phases that have no count and of the
ones still to come. A failed run gets a hint (HINTS) by the error code its command reported.
"""
import json
import os
import statistics
import subprocess
import threading
import time
import traceback

from paretogpu.ui import api, presets
from paretogpu.ui.api import STATE, load_json as _load, save_json as _save
from paretogpu.views import reporter

JOBS = os.path.join(STATE, "jobs")
LOG_LINES = 50000  # kept in memory per run; the file has all of them
ROOT = presets.ROOT


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
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise ValueError("уже идёт запуск: дождитесь его конца или остановите")
        plan, frame_dir = presets.plan(preset_id, dict(values), sch)
        steps, report = [], None
        for cmd, v in plan:
            steps.append({"cmd": cmd, "argv": presets.argv_of(cmd, v, sch), "values": v, "status": "pending",
                          "started": None, "finished": None, "rc": None,
                          "phases": [{"id": p, "status": "pending"} for p in (presets.phases_of(cmd, v) or ["run"])]})
            report = presets.report_of(cmd, v) or report
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
                    job["report"] = api.out_url(rp) or f"/jobreport/{job['id']}"  # --out outside paretogpu/out
                    job["summary"] = api.cost_summary(os.path.dirname(rp))
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
                    if line.startswith(reporter.PREFIX):
                        try:
                            self._event(s, json.loads(line[len(reporter.PREFIX):]))
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
        elif ev == "error":
            s["error_code"] = e.get("code")

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


# known failures: (error code the command reports (model/errors.py), text of the log that names it when there is no
# code, what to do, in words for someone who has not seen the CLI; `action`: a button the panel offers)
HINTS = [
    ("unity_cli_off", "No Pipeline instance found", "Unity CLI в редакторе отключился. Включите его в Unity заново "
     "(пакет com.unity.pipeline) и запустите ещё раз: ожидание не поможет.", None),
    ("editor_busy", "does not answer Unity CLI or is busy", "Unity не отвечает: откройте проект в Unity и дождитесь "
     "конца компиляции и импорта (полоса прогресса внизу редактора), затем запустите ещё раз.", "no_compile"),
    ("rd_not_game_frame", "is not the game frame", "RenderDoc несколько раз подряд записал только окно редактора, а не "
     "кадр игры. Сделайте вкладку Game видимой (не за другой вкладкой, окно Unity не свёрнуто) и запустите проверку "
     "ещё раз.", None),
    ("not_playing", "is not in Play Mode", "Unity не в Play Mode: кадр в редакторе без запущенной игры не тот, что на "
     "устройстве. Включите Play Mode, выставьте нужный кадр в окне Game и снимите кадр ещё раз.", None),
    ("no_frame", "did not capture a frame", "Frame Debugger не получил кадр игры. Включите в Unity Play Mode, сделайте "
     "вкладку Game видимой (не за другой вкладкой, окно Unity не свёрнуто) и снимите кадр ещё раз.", None),
    ("renderdoc", "renderdoc", "Не получилось снять кадр через RenderDoc. В Unity на вкладке Game нажмите правой кнопкой "
     "мыши → Load RenderDoc и запустите проверку ещё раз.", None),
    ("malioc", "malioc", "Не найден или упал malioc (Arm Performance Studio). Запустите start.bat: он покажет, чего не "
     "хватает.", None),
    ("no_snapshot", "no frame_events.json", "Снимок не найден или не дописан: снимите кадр заново.", None),
]


def hint_of(job):
    codes = [s.get("error_code") for s in job["steps"] if s.get("error_code")]
    text = ("\n".join(job["log"][-400:]) + "\n" + (job.get("error") or "")).lower()
    for code, needle, hint, action in HINTS:
        if code in codes or (not codes and needle.lower() in text):
            return {"text": hint, "action": action if any(s["cmd"] == "cost" for s in job["steps"]) else None}
    for code, needle, hint, action in HINTS:
        if codes and needle.lower() in text:
            return {"text": hint, "action": action if any(s["cmd"] == "cost" for s in job["steps"]) else None}
    return {"text": "Что-то пошло не так, подробности в логе ниже. Если непонятно, пришлите лог тому, "
                    "кто поддерживает ParetoGPU.", "action": None}


def fmt_time(s):
    s = int(s)
    return f"{s // 60} мин {s % 60} с" if s >= 60 else f"{s} с"
