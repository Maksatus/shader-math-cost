"""Exact shader variants compiled in the open editor (cs/ParetoGpuVariants.cs through bridge.py).

ShaderData.Pass.CompileVariant for Android: the same files as "Compile and show code", including shader_feature
variants it skips. One Unity run compiles a list of variants into a raw folder: <id>_<platform>_<stage>.bin as it
goes, and answers variants.json at the end ({"variants": [{"id", "files", "errors", "fingerprint"}]}).

  compile_variants(project, keys, raw, platforms)  -> the result (the caller places the files)
  shader_query(project, "Fingerprints" | "Passes", shaders, raw)  -> {shader name: answer}
  unfinished_run(raw)  -> (keys, result) of a run that did not finish (the editor crashed or the connection broke)
"""
import json
import os
import re

from paretogpu.adapters.unity import bridge
from paretogpu.adapters.unity.cli import UnityError
from paretogpu.model.variant import NOT_FINISHED, VariantKey

SCRIPT = "ParetoGpuVariants.cs"
JOB = "variants"


class VariantsError(UnityError):
    code = "variants"


def shader_query(project, entry, shaders, raw, timeout=600):
    """{shader name: answer} of a ParetoGpuVariants entry that takes {"shaders"} (Fingerprints, Passes) in the
    open editor."""
    return bridge.call(project, SCRIPT, entry, raw, entry.lower(), {"shaders": sorted(set(shaders))}, VariantsError,
                       timeout)["shaders"]


def current_fingerprints(project, shaders, raw, timeout=600):
    """{shader name: fingerprint (None: not found)} from the open editor (ParetoGpuVariants.Fingerprints)."""
    return shader_query(project, "Fingerprints", shaders, raw, timeout)


def compile_variants(project, keys, raw, platforms, timeout=1800, on_start=None, on_step=None):
    """Compile `keys` in the open editor into `raw`; returns the result of the run.
    on_start(total) once the editor answers; on_step(done, total): variants compiled so far."""
    bridge.require_editor(project, VariantsError, "it is needed to compile the variants of the frame")
    os.makedirs(raw, exist_ok=True)
    for fn in os.listdir(raw):
        os.remove(os.path.join(raw, fn))
    if on_start:
        on_start(len(keys))

    def progress(text):
        m = re.fullmatch(r"variants (\d+)/(\d+)", text)
        if m and on_step:
            on_step(int(m.group(1)), int(m.group(2)))

    config = {"shaders": [k.shader for k in keys], "subshaders": [k.subshader for k in keys],
              "pass_indices": [k.pass_index for k in keys], "passes": [k.pass_name for k in keys],
              "keywords": [" ".join(k.keywords) for k in keys], "platforms": list(platforms)}
    result = bridge.call(project, SCRIPT, "Run", raw, JOB, config, VariantsError, timeout, progress)
    if len(result["variants"]) != len(keys):
        raise VariantsError(f"Unity compiled {len(result['variants'])} of {len(keys)} variants")
    return result


def unfinished_run(raw):
    """(keys, result) of a Unity run that did not finish: it left its compiled files in `raw` without
    its answer; variants without any file were not reached (or failed: the messages are lost with the run). None if
    there is no such run."""
    config = os.path.join(raw, f"{JOB}_config.json")
    if not os.path.exists(config) or os.path.exists(os.path.join(raw, f"{JOB}.json")):
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
    with open(os.path.join(raw, f"{JOB}.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({**result, "recovered": True}, f, indent=1)
