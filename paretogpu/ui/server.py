"""Local web UI: `python -m paretogpu ui`.

One window (Edge in app mode, else the default browser) on http://127.0.0.1:<port>:
  /            app.html: tabs "function costs" (docs/, the site as on GitHub), "project test" (the commands with
               their progress), "reports" (the snapshots in paretogpu/out)
  /site/...    docs/
  /out/...     paretogpu/out: reports of the project's frames; served only here, they never reach git or the site
  /api/...     JSON
What a button runs: ui/presets.py; running it with its progress: ui/runner.py; what the pages read: ui/api.py.
"""
import http.server
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

from paretogpu.app.results import adopt
from paretogpu.core import compare
from paretogpu.features import RESULT_KINDS
from paretogpu.store import cost_runs as runs_store
from paretogpu.store import workspace
from paretogpu.ui import api, presets
from paretogpu.ui.api import DOCS, OUT
from paretogpu.ui.runner import Runner
from paretogpu.views import html

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = presets.PRESETS


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
                return self._json(api.options())
            if p == "/api/job":
                return self._json({"job": RUNNER.view(q.get("id"), int(q.get("since", 0)))})
            if p == "/api/jobs":
                with RUNNER.lock:
                    past = list(reversed(RUNNER.past))
                    cur = RUNNER.summary(RUNNER.job) if RUNNER.job else None
                return self._json({"jobs": past, "current": cur})
            if p == "/api/reports":
                return self._json({"reports": api.snapshots()})
            if p == "/api/costs":
                return self._json({"runs": api.cost_runs()})
            if p == "/api/results":
                return self._json({"items": api.results(q.get("kind"))})
            if p == "/api/matcompares":
                return self._json({"items": api.matcompares()})
            if p == "/api/matshaders":
                return self._json({"items": api.matshaders()})
            if p == "/api/materials":
                if not os.path.isdir(os.path.join(q.get("project", ""), "Assets")):
                    return self._json({"error": "нет такого Unity-проекта", "materials": []}, 404)
                return self._json({"materials": api.project_materials(q["project"])})
            if p == "/compare":
                fa, fb = api.run_file(q.get("a")), api.run_file(q.get("b"))
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
                return self._json({"checks": api.doctor_checks(), "site": api.site_info()})
            if p == "/api/editor":
                return self._json(api.editor_status(q.get("project", "")))
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
                api.remember_values(body.get("preset"), values)
                return self._json({"id": job_id})
            if p == "/api/material_match":
                project = body.get("project") or ""
                if not os.path.isdir(os.path.join(project, "Assets")):
                    return self._json({"error": "нет такого Unity-проекта"}, 404)
                return self._json({"paths": api.material_by_content(project, body.get("name") or "", body.get("text") or "")})
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
    adopt(RESULT_KINDS)
    RUNNER = Runner()
    SCHEMA = presets.schema()
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
