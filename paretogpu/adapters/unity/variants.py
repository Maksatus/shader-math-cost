"""Exact shader variants compiled in the open editor (cs/ParetoGpuVariants.cs): Vulkan "Compile and show code" text per
shader, assembled here (compiled_text.py); only the variants a build has. A run writes <id>_vulkan_<stage>.bin into
`raw`, then variants.json {"variants": [{"id", "files", "errors", "fingerprint"}]}; the caller places the files."""
import json
import os
import re

from paretogpu.adapters.renderdoc import spirv_as
from paretogpu.adapters.unity import bridge
from paretogpu.adapters.unity import compiled_text
from paretogpu.adapters.unity.cli import UnityError
from paretogpu.model.variant import NOT_FINISHED, NOT_IN_BUILD, STOPPED, VariantKey

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
    """-> {"shaders": {shader name: fingerprint, None if not found}, "pipeline": {"name", "path", "hash"} active}."""
    return bridge.call(project, SCRIPT, "Fingerprints", raw, "fingerprints", {"shaders": sorted(set(shaders))},
                       VariantsError, timeout)


def compile_variants(project, keys, raw, timeout=1800, on_start=None, on_step=None):
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
              "keywords": [" ".join(k.keywords) for k in keys]}
    result = bridge.call(project, SCRIPT, "Run", raw, JOB, config, VariantsError, timeout, progress)
    if len(result["variants"]) != len(keys):
        raise VariantsError(f"Unity compiled {len(result['variants'])} of {len(keys)} variants")
    _assemble_texts(raw, keys, result)
    return result


def _assemble_texts(raw, keys, result):
    """Vulkan "text" entries -> <id>_vulkan_<stage>.bin; a variant the text lacks gets an error."""
    parsed, tool = {}, None
    for v in result["variants"]:
        k, files = keys[v["id"]], []
        for x in v["files"]:
            if x["text"] not in parsed:
                with open(os.path.join(raw, x["text"]), encoding="utf-8", errors="replace") as f:
                    parsed[x["text"]] = compiled_text.blocks(f.read())
            code = parsed[x["text"]].get((k.subshader, k.pass_index, frozenset(k.keywords), x["stage"]))
            if code is None:
                v["errors"].append(f"vulkan {x['stage']}: {not_in_build(k, x['stage'], parsed[x['text']])}")
                continue
            fn = f"{v['id']}_vulkan_{x['stage']}.bin"
            try:
                tool = tool or spirv_as()
                compiled_text.assemble(code, os.path.join(raw, fn), tool, compiled_text.version_of(code))
            except (compiled_text.CompiledTextError, OSError) as e:
                v["errors"].append(f"vulkan {x['stage']}: {e}")
                continue
            files.append({"stage": x["stage"], "file": fn})
        v["files"] = files


def not_in_build(k, stage, blocks):
    """Why k's code is not in the text: the frame's keywords no build variant of its pass has, else the combination."""
    built = [kw for (ss, pi, kw, st) in blocks if (ss, pi, st) == (k.subshader, k.pass_index, stage)]
    if not built:
        return f"{NOT_IN_BUILD}: the build has no variant of this pass"
    extra = [w for w in k.keywords if not any(w in kw for kw in built)]
    if extra:
        return f"{NOT_IN_BUILD}: keywords of the frame no build variant of the pass has: {' '.join(extra)}"
    return f"{NOT_IN_BUILD}: no build variant of the pass has this combination of the frame's keywords"


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
        m = re.fullmatch(r"(\d+)_vulkan_(vert|frag|comp)\.bin", fn)
        if m:
            files.setdefault(int(m.group(1)), []).append({"stage": m.group(2), "file": fn})
    stop = next((i for i in range(len(keys)) if i not in files), None)
    result = {"variants": [{"id": i, "files": files.get(i, []), "fingerprint": None,
                            "errors": [] if i in files else [NOT_FINISHED] + ([STOPPED] if i == stop else [])}
                           for i in range(len(keys))]}
    return keys, result


def mark_placed(raw):
    with open(os.path.join(raw, PLACED), "w", encoding="utf-8") as f:
        f.write("1")
