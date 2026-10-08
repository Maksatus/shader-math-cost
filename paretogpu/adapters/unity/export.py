"""Compile shaders of a Unity project into per-variant files for malioc (plan item A2.2).

Runs cs/ParetoGpuExport.cs inside Unity, which does what Inspector ->
"Compile and show code" does (variants that would be included into the build),
then splits the result with split.py:
  <out>/<shader>/          GLSL (.vert / .frag) for GLES3
  <out>/<shader>_vulkan/   SPIR-V (.vert.spv / .frag.spv) for Vulkan

How Unity is reached (mode="auto"):
  1. the project is open in an editor and Unity CLI (com.unity.pipeline) answers ->
     `unity command run_script` in that editor: nothing is written to the project;
  2. the project is not open -> Unity.exe -batchmode -executeMethod; the script is
     copied into Assets/Editor for the run and removed afterwards;
  3. the project is open but the editor does not answer -> error.
"""
import json
import os
import shutil
import subprocess
import time

from paretogpu.adapters.unity import split as split_unity
from paretogpu.adapters.unity.cli import (UnityError, code_of, editor_ready, errors_of, project_open, run_script,
                                         script, unity_exe)

SCRIPT = script("ParetoGpuExport.cs")
TEMP_DIR = "ParetoGpuExportTemp"  # Assets/Editor/<this> during a batchmode run


class ExportError(UnityError):
    code = "export"


def run_in_editor(project, config, timeout):
    d = run_script(project, SCRIPT, "ParetoGpuExport.Run", [config], timeout * 1000, timeout + 60, timeout)
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success"):
        diag = "; ".join(str(x) for x in res.get("diagnostics") or [])
        raise ExportError(f"run_script failed: {diag or errors_of(d)}", code_of(d))
    return json.loads(res["result"])


def run_batch(project, config, out, timeout):
    exe = unity_exe(project)
    editor_dir = os.path.join(project, "Assets", "Editor")
    tmp = os.path.join(editor_dir, TEMP_DIR)
    created_editor = not os.path.exists(editor_dir)
    if os.path.exists(tmp):
        raise ExportError(f"{tmp} exists (left from an interrupted run?): remove it and retry")
    os.makedirs(tmp)
    log = os.path.join(out, "unity_batch.log")
    try:
        shutil.copy(SCRIPT, tmp)
        try:
            r = subprocess.run([exe, "-batchmode", "-quit", "-projectPath", project,
                                "-executeMethod", "ParetoGpuExport.Batch", "-paretogpuConfig", config,
                                "-logFile", log], timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ExportError(f"Unity did not finish in {timeout} s, see {log}") from None
    finally:
        # remove the script and the .meta files Unity created for it
        shutil.rmtree(tmp, ignore_errors=True)
        for p in (tmp + ".meta",) + ((editor_dir + ".meta",) if created_editor else ()):
            if os.path.exists(p):
                os.remove(p)
        if created_editor and os.path.isdir(editor_dir) and not os.listdir(editor_dir):
            os.rmdir(editor_dir)
    result = os.path.join(out, "export.json")
    if not os.path.exists(result):
        raise ExportError(f"Unity exited with code {r.returncode} without export.json, see {log}")
    with open(result, encoding="utf-8") as f:
        return json.load(f)


def export(project, shaders, out, platforms=("gles3", "vulkan"), mode="auto", timeout=1800):
    """Compile `shaders` (asset paths, folders under Assets/ or shader names) and split them.
    Returns {"mode", "unity", "items": [{shader, asset, file, error, outputs: {dir: n files}, split_errors}],
    "errors"}."""
    project = os.path.abspath(project)
    out = os.path.abspath(out)
    raw = os.path.join(out, "_compiled")
    os.makedirs(raw, exist_ok=True)
    # results of an earlier run must not pass for this one's (Unity may fail before writing anything)
    for fn in os.listdir(raw):
        if os.path.isfile(os.path.join(raw, fn)):
            os.remove(os.path.join(raw, fn))
    config = os.path.join(raw, "config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"shaders": list(shaders), "platforms": list(platforms), "out": raw}, f, indent=1)

    if mode == "auto":
        if editor_ready(project):
            mode = "editor"
        elif project_open(project):
            raise ExportError(f"{project} is open in Unity, but the editor does not answer Unity CLI "
                              "(com.unity.pipeline): wait until it is idle, or close it to use batchmode")
        else:
            mode = "batch"
    t = time.time()
    try:
        res = run_in_editor(project, config, timeout) if mode == "editor" else run_batch(project, config, raw, timeout)
    except json.JSONDecodeError as e:
        raise ExportError(f"Unity returned a broken result: {e}") from None
    except subprocess.TimeoutExpired:
        raise ExportError(f"Unity did not answer in {timeout} s") from None
    res["mode"], res["seconds"] = mode, round(time.time() - t, 1)

    for item in res.get("items", []):
        if item.get("error") or not item.get("file"):
            continue
        _, outputs, errs = split_unity.split(item["file"], out)
        item["outputs"] = {d: len(m) for d, m in outputs.items()}
        item["split_errors"] = errs
    return res
