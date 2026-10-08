"""Unity: the editor of a project, Unity CLI (com.unity.pipeline) and the C# scripts run in the open editor.

  editor_ready(project)  the open editor answers Unity CLI and is idle
  run_script(...)        `unity command run_script` of one of cs/*.cs in that editor
  unity_exe(project)     Unity.exe of the project's version (batchmode)
"""
import json
import os
import re
import shutil
import subprocess

from paretogpu.model.errors import ParetoError

HERE = os.path.dirname(os.path.abspath(__file__))
CS = os.path.join(HERE, "cs")
HUB_EDITORS = r"C:\Program Files\Unity\Hub\Editor"


class UnityError(ParetoError):
    code = "unity"


def script(name):
    """Absolute path of a C# script of cs/ (Unity resolves relative paths from its project)."""
    return os.path.join(CS, name)


def unity_cli():
    return shutil.which("unity")


def editor_version(project):
    with open(os.path.join(project, "ProjectSettings", "ProjectVersion.txt"), encoding="utf-8") as f:
        m = re.search(r"m_EditorVersion:\s*(\S+)", f.read())
    if not m:
        raise UnityError(f"no m_EditorVersion in {project}/ProjectSettings/ProjectVersion.txt")
    return m.group(1)


def hub_editor_dirs():
    """Folders Unity Hub installs editors into: its "install location" setting (secondaryInstallPath.json), the default."""
    dirs = []
    try:
        with open(os.path.join(os.environ.get("APPDATA", ""), "UnityHub", "secondaryInstallPath.json"),
                  encoding="utf-8") as f:
            p = json.load(f)
        if isinstance(p, str) and p:
            dirs.append(p)
    except (OSError, ValueError):
        pass
    return dirs + [HUB_EDITORS]


def unity_exe(project):
    ver = editor_version(project)
    cands = [os.path.join(d, ver, "Editor", "Unity.exe") for d in hub_editor_dirs()]
    exe = os.environ.get("UNITY_EDITOR") or next((c for c in cands if os.path.exists(c)), cands[-1])
    if not os.path.exists(exe):
        raise UnityError(f"Unity {editor_version(project)} not found at {exe} (set UNITY_EDITOR)")
    return exe


def project_open(project):
    """True if an editor holds the project's lock file."""
    lock = os.path.join(project, "Temp", "UnityLockfile")
    if not os.path.exists(lock):
        return False
    try:  # the editor keeps the file open without sharing; a stale lock can be opened
        with open(lock, "a"):
            return False
    except OSError:
        return True


def cli_json(args, timeout):
    r = subprocess.run([unity_cli(), *args, "--no-banner", "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"success": False, "errors": [{"message": (r.stdout + r.stderr).strip()[-2000:]}]}


def run_script(project, path, entry, args, timeout_ms, wait, timeout=None):
    """`unity command run_script` of the C# file `path` in the open editor of `project`: its JSON answer.
    timeout_ms: how long the editor may run the entry; wait: seconds this process waits for the CLI."""
    cmd = ["command", "run_script", "--project-path", project]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    cmd += ["--timeout_ms", str(timeout_ms), "--file", path, "--entry", entry, "--args", json.dumps(args)]
    return cli_json(cmd, wait)


def editor_ready(project, allow_play=False):
    """The project's editor answers Unity CLI and is idle (or in Play Mode, if allow_play)."""
    if not unity_cli():
        return False
    try:
        d = cli_json(["command", "editor_status", "--project-path", project], 60)
    except subprocess.TimeoutExpired:
        return False
    res = ((d.get("data") or {}).get("result") or {}) if d.get("success") else {}
    ok = ("ready", "playing", "paused") if allow_play else ("ready",)
    return res.get("status") in ok and not res.get("compiling") and not res.get("domainReloadInProgress")


def errors_of(d):
    return "; ".join(e.get("message", "") for e in d.get("errors") or []) or json.dumps(d)[:2000]
