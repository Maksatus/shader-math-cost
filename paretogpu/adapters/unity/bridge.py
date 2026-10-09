"""The ParetoGPU scripts of cs/ in the open editor, one way for all of them (cs/ParetoGpuJob.cs is the protocol).

Python writes <out>/<name>_config.json, runs the entry with Unity CLI (`unity command run_script`) and reads the
answer <out>/<name>.json; <name>.progress goes to on_progress while it runs, <name>.error ("[code] message") is
raised as the caller's error with that code. An entry that returns "started" works on editor ticks: the answer is
waited for.

  call(project, script, entry, out, name, config, error)  -> the answer
  run(project, script, entry, args, error)                -> what an entry outside the protocol returns
  require_editor(project, error, why)                     the editor answers Unity CLI and is idle (or playing)
  source(script)                                          the file Unity compiles: ParetoGpuJob.cs + the script
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time

from paretogpu.adapters.unity.cli import CS, cli_json, editor_ready

JOB = "ParetoGpuJob.cs"
BUILD = os.path.join(tempfile.gettempdir(), "paretogpu", "cs")
USING = re.compile(r"^using [\w.]+;\s*$")
FAILURE = re.compile(r"\[(\w+)\] ")


def source(script):
    """cs/ParetoGpuJob.cs and cs/<script> as one file (run_script compiles a single file): the `using` lines of both
    first. The file is named after the script, in a folder named by its content."""
    usings, bodies = [], []
    for fn in (JOB, script):
        with open(os.path.join(CS, fn), encoding="utf-8") as f:
            lines = f.read().splitlines()
        usings += [x.strip() for x in lines if USING.match(x)]
        bodies.append("\n".join(x for x in lines if not USING.match(x)).strip("\n"))
    text = "\n".join(dict.fromkeys(usings)) + "\n\n" + "\n\n".join(bodies) + "\n"
    folder = os.path.join(BUILD, hashlib.sha1(text.encode()).hexdigest()[:12])
    path = os.path.join(folder, script)
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    return path


def errors_of(d):
    return "; ".join(e.get("message", "") for e in d.get("errors") or []) or json.dumps(d)[:2000]


def code_of(d):
    """The error code of a failed Unity CLI answer (model/errors.py), or None for the caller's own."""
    if "No Pipeline instance found" in json.dumps(d):
        return "unity_cli_off"
    return None


def require_editor(project, error, why, allow_play=True):
    if not editor_ready(project, allow_play):
        raise error(f"the editor of {project} does not answer Unity CLI or is busy (compiling, importing): {why}",
                    "editor_busy")


def run(project, script, entry, args, error, timeout=120):
    """`unity command run_script` of <Class>.<entry> of cs/<script>: the string it returns. timeout: seconds the
    editor may run it."""
    cls = os.path.splitext(script)[0]
    try:
        d = cli_json(["command", "run_script", "--project-path", project, "--timeout", str(timeout),
                      "--timeout_ms", str(timeout * 1000), "--file", source(script), "--entry", f"{cls}.{entry}",
                      "--args", json.dumps(args)], timeout + 60)
    except subprocess.TimeoutExpired:
        raise error(f"{entry}: Unity CLI did not answer in {timeout + 60} s") from None
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise error(f"{entry}: {diag or res.get('errorDetails') or errors_of(d)}"[:3000], code_of(d))
    return res.get("result")


def failure(path, error):
    """The error of a <name>.error file: "[code] message"."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = FAILURE.match(text)
    code = m.group(1) if m and m.group(1) != "error" else None
    return error((text[m.end():] if m else text)[-3000:], code)


class _Progress(threading.Thread):
    """on_progress(text) for every new text of <name>.progress."""

    def __init__(self, path, on_progress):
        super().__init__(daemon=True)
        self.path, self.on_progress, self.stop = path, on_progress, threading.Event()
        self.last = None

    def poll(self):
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            return
        if text and text != self.last:
            self.last = text
            self.on_progress(text)

    def run(self):
        while not self.stop.wait(0.5):
            self.poll()


def call(project, script, entry, out, name, config, error, timeout=600, on_progress=None):
    """Run <Class>.<entry> of cs/<script> on the job <out>/<name> with `config`; returns its answer (parsed JSON)."""
    out = os.path.abspath(out)  # Unity resolves relative paths from its project
    os.makedirs(out, exist_ok=True)
    paths = {k: os.path.join(out, f"{name}.{k}") for k in ("json", "progress", "error")}
    for p in paths.values():
        if os.path.exists(p):
            os.remove(p)
    cfg = os.path.join(out, f"{name}_config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({**config, "out": out, "name": name}, f, indent=1)
    watch = _Progress(paths["progress"], on_progress or (lambda text: None))
    watch.start()
    try:
        result = run(project, script, entry, [cfg], error, timeout)
        t = time.time()
        while result == "started" and not any(os.path.exists(paths[k]) for k in ("json", "error")):
            if time.time() - t > timeout:
                raise error(f"{entry}: no answer after {timeout} s (progress: {watch.last})")
            time.sleep(0.5)
    finally:
        watch.stop.set()
        watch.join()
    watch.poll()
    if os.path.exists(paths["error"]):
        raise failure(paths["error"], error)
    if result not in ("ok", "started") or not os.path.exists(paths["json"]):
        raise error(f"{entry}: {result!r} without an answer in {paths['json']}")
    with open(paths["json"], encoding="utf-8") as f:
        return json.load(f)
