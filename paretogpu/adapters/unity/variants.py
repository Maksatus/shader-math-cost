"""Exact shader variants compiled in the open editor (cs/ParetoGpuVariants.cs).
ShaderData.Pass.CompileVariant for Android gives the same files as "Compile and show code", including the shader_feature
variants it skips. A run writes <id>_<platform>_<stage>.bin into `raw` as it goes, then variants.json
{"variants": [{"id", "files", "errors", "fingerprint"}]}; the caller places the files."""
import json
import os
import re

from paretogpu.adapters.unity import bridge
from paretogpu.adapters.unity.cli import UnityError
from paretogpu.model.variant import NOT_FINISHED, STOPPED, VariantKey

SCRIPT = "ParetoGpuVariants.cs"
JOB = "variants"
PLACED = f"{JOB}.placed"


class VariantsError(UnityError):
    code = "variants"


def shader_query(project, entry, shaders, raw, timeout=600):
    """-> {shader name: answer} of an entry that takes {"shaders"} (Fingerprints, Passes)."""
    return bridge.call(project, SCRIPT, entry, raw, entry.lower(), {"shaders": sorted(set(shaders))}, VariantsError,
                       timeout)["shaders"]


def current_fingerprints(project, shaders, raw, timeout=600):
    """-> {shader name: fingerprint, None if not found}."""
    return shader_query(project, "Fingerprints", shaders, raw, timeout)


def compile_variants(project, keys, raw, platforms, timeout=1800, on_start=None, on_step=None):
    """on_start(total) once the editor answers; on_step(done, total): variants compiled so far."""
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


def _config_keys(raw):
    config = os.path.join(raw, f"{JOB}_config.json")
    if not os.path.exists(config):
        return None
    with open(config, encoding="utf-8") as f:
        cfg = json.load(f)
    return [VariantKey(s, ss, pi, p, tuple(kw.split())) for s, ss, pi, p, kw in
            zip(cfg["shaders"], cfg["subshaders"], cfg["pass_indices"], cfg["passes"], cfg["keywords"])]


def unfinished_run(raw):
    """(keys, result) of a run whose answer was not placed (editor crash, broken connection, or finished after Python
    gave up). Unfinished: variants without files were not reached, the first one gets STOPPED. None if no such run."""
    keys = _config_keys(raw)
    answer = os.path.join(raw, f"{JOB}.json")
    if keys is None or os.path.exists(os.path.join(raw, PLACED)):
        return None
    if os.path.exists(answer):
        with open(answer, encoding="utf-8") as f:
            result = json.load(f)
        fits = not result.get("recovered") and len(result.get("variants") or []) == len(keys)
        return (keys, result) if fits else None
    files = {}
    for fn in os.listdir(raw):
        m = re.fullmatch(r"(\d+)_(gles3|vulkan)_(vert|frag|comp)\.bin", fn)
        if m:
            files.setdefault(int(m.group(1)), []).append({"platform": m.group(2), "stage": m.group(3), "file": fn})
    stop = next((i for i in range(len(keys)) if i not in files), None)
    result = {"variants": [{"id": i, "files": files.get(i, []), "fingerprint": None,
                            "errors": [] if i in files else [NOT_FINISHED] + ([STOPPED] if i == stop else [])}
                           for i in range(len(keys))]}
    return keys, result


def mark_placed(raw):
    with open(os.path.join(raw, PLACED), "w", encoding="utf-8") as f:
        f.write("1")
