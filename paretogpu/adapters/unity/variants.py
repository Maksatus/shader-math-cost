"""Exact shader variants compiled in the open editor (cs/ParetoGpuVariants.cs).

ShaderData.Pass.CompileVariant for Android: the same files as "Compile and show code", including shader_feature
variants it skips. One Unity run compiles a list of variants into a raw folder: <id>_<platform>_<stage>.bin as it
goes, and variants_result.json at the end ({"variants": [{"id", "files", "errors", "fingerprint"}]}).

  compile_variants(project, keys, raw, platforms)  -> the result (the caller places the files)
  shader_query(project, "Fingerprints" | "Passes", shaders, raw)  -> {shader name: answer}
  unfinished_run(raw)  -> (keys, result) of a run that did not finish (the editor crashed or the connection broke)
"""
import json
import os
import re
import threading

from paretogpu.adapters.unity.cli import UnityError, editor_ready, errors_of, run_script, script
from paretogpu.model.variant import NOT_FINISHED, VariantKey

SCRIPT = script("ParetoGpuVariants.cs")


class VariantsError(UnityError):
    code = "variants"


class EditorBusy(VariantsError):
    code = "editor_busy"


def _check(d, prefix=""):
    res = (d.get("data") or {}).get("result") or {}
    if not d.get("success") or not res.get("success") or not str(res.get("result", "")).startswith("ok"):
        diag = "; ".join(x.get("message", "") for x in res.get("diagnostics") or [] if x.get("severity") == "error")
        raise VariantsError(f"{prefix}{res.get('result') or diag or res.get('errorDetails') or errors_of(d)}"[:3000])


def shader_query(project, entry, shaders, raw, timeout=600):
    """{shader name: answer} of a ParetoGpuVariants entry that takes {"shaders", "out"} (Fingerprints, Passes) in the
    open editor."""
    raw = os.path.abspath(raw)  # Unity resolves relative paths from its project
    os.makedirs(raw, exist_ok=True)
    config, out = os.path.join(raw, f"{entry.lower()}_config.json"), os.path.join(raw, f"{entry.lower()}.json")
    if os.path.exists(out):
        os.remove(out)
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"shaders": sorted(set(shaders)), "out": out}, f, indent=1)
    d = run_script(project, SCRIPT, f"ParetoGpuVariants.{entry}", [config], timeout * 1000, timeout + 60, timeout)
    _check(d, f"{entry}: ")
    with open(out, encoding="utf-8") as f:
        return json.load(f)["shaders"]


def current_fingerprints(project, shaders, raw, timeout=600):
    """{shader name: fingerprint (None: not found)} from the open editor (ParetoGpuVariants.Fingerprints)."""
    return shader_query(project, "Fingerprints", shaders, raw, timeout)


def compile_variants(project, keys, raw, platforms, timeout=1800, on_start=None, on_step=None):
    """Compile `keys` in the open editor into `raw`; returns the result of the run (variants_result.json).
    on_start(total) once the editor answers; on_step(done, total): variants with a file written so far."""
    if not editor_ready(project, allow_play=True):
        raise EditorBusy(f"the editor of {project} does not answer Unity CLI or is busy (compiling, importing): "
                         "it is needed to compile the variants of the frame")
    os.makedirs(raw, exist_ok=True)
    for fn in os.listdir(raw):
        os.remove(os.path.join(raw, fn))
    config = os.path.join(raw, "config.json")
    if on_start:
        on_start(len(keys))
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"shaders": [k.shader for k in keys], "subshaders": [k.subshader for k in keys],
                   "pass_indices": [k.pass_index for k in keys], "passes": [k.pass_name for k in keys],
                   "keywords": [" ".join(k.keywords) for k in keys], "platforms": list(platforms), "out": raw}, f,
                  indent=1)
    stop = threading.Event()
    watch = threading.Thread(target=_watch_compiled, args=(raw, len(keys), stop, on_step), daemon=True)
    watch.start()
    try:
        d = run_script(project, SCRIPT, "ParetoGpuVariants.Run", [config], timeout * 1000, timeout + 60, timeout)
    finally:
        stop.set()
    _check(d)
    with open(os.path.join(raw, "variants_result.json"), encoding="utf-8") as f:
        result = json.load(f)
    if len(result["variants"]) != len(keys):
        raise VariantsError(f"Unity compiled {len(result['variants'])} of {len(keys)} variants")
    return result


def _watch_compiled(raw, total, stop, on_step):
    """Progress of a Unity run: variants with a file written so far (ParetoGpuVariants.cs writes <id>_*.bin as it
    goes; a variant that fails writes nothing, so the count may lag behind)."""
    last = -1
    while not stop.wait(0.5):
        try:
            done = len({fn.split("_", 1)[0] for fn in os.listdir(raw) if fn.endswith(".bin")})
        except OSError:
            continue
        if done != last:
            if on_step:
                on_step(done, total)
            last = done


def unfinished_run(raw):
    """(keys, result) of a Unity run that did not finish: it left its compiled files in `raw` without
    variants_result.json; variants without any file were not reached (or failed: the messages are lost with the
    run). None if there is no such run."""
    config = os.path.join(raw, "config.json")
    if not os.path.exists(config) or os.path.exists(os.path.join(raw, "variants_result.json")):
        return None
    with open(config, encoding="utf-8") as f:
        cfg = json.load(f)
    keys = [VariantKey(s, ss, pi, p, tuple(kw.split())) for s, ss, pi, p, kw in
            zip(cfg["shaders"], cfg["subshaders"], cfg["pass_indices"], cfg["passes"], cfg["keywords"])]
    files = {}
    for fn in os.listdir(raw):
        m = re.fullmatch(r"(\d+)_(gles3|vulkan)_(vert|frag|comp)\.bin", fn)
        if m:
            files.setdefault(int(m.group(1)), []).append({"platform": m.group(2), "stage": m.group(3), "file": fn})
    result = {"variants": [{"id": i, "files": files.get(i, []), "fingerprint": None,
                            "errors": [] if i in files else [NOT_FINISHED]}
                           for i in range(len(keys))]}
    return keys, result


def mark_recovered(raw, result):
    """Write the result of an unfinished run once its files are placed: it is not recovered again."""
    with open(os.path.join(raw, "variants_result.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({**result, "recovered": True}, f, indent=1)
