"""A shader variant: what a draw or dispatch of a frame ran, and how its compiled files are named.

key_of(event) -> (shader, subshader, pass index, pass name, keywords) of a draw; (compute shader, -1, 0, kernel, ())
of a dispatch. The compiled files of a variant live in <variants>/<shader>/ (GLSL for GLES3) and
<variants>/<shader>_vulkan/ (SPIR-V), named by base_name(): readable, unique, short enough for Windows paths.
"""
import hashlib
import json
import re

EXT = {("gles3", "vert"): ".vert", ("gles3", "frag"): ".frag", ("gles3", "comp"): ".comp",
       ("vulkan", "vert"): ".vert.spv", ("vulkan", "frag"): ".frag.spv", ("vulkan", "comp"): ".comp.spv"}
API = {"gles3": "gles", "vulkan": "vulkan"}  # export platform -> malioc API
STAGE = {"vert": "vertex", "frag": "fragment", "comp": "compute"}
SHORT = {v: k for k, v in STAGE.items()}
COMPUTE = -1  # subshader of a compute kernel key
# ComputeShader.FindKernel / HasKernel and ShaderUtil.CompileComputeShaderVariant called from a run_script crashed
# the 6000.3.18f1 editor three times (2026-10-06), on built-in and package compute shaders alike
COMPUTE_NOT_COMPILED = "compute kernel not compiled: compiling compute shaders from a script crashes Unity 6000.3"
NOT_FINISHED = "no output: the Unity run did not finish"  # transient: compiled again next time


def slug(s):
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_") or "none"


def key_of(ev):
    """Variant of a draw event: (shader, subshader, pass index, pass name, keywords);
    of a compute event: (compute shader, -1, 0, kernel, ())."""
    if ev["kind"] == "compute":
        c = ev.get("compute") or {}
        return (c.get("shader") or "", COMPUTE, 0, c.get("kernel") or "", ())
    return (ev["shader"], ev["subshader"] or 0, ev["pass_index"] or 0, ev["pass"] or "", tuple(sorted(ev["keywords"])))


def stages_of(k):
    return ("comp",) if k[1] == COMPUTE else ("vert", "frag")


def key_str(k):
    if k[1] == COMPUTE:
        return f"{k[0]} | kernel {k[3]}"
    return f"{k[0]} | {k[1]}.{k[2]} {k[3]} | {' '.join(k[4]) or '-'}"


def base_name(k):
    """File name stem of a variant: readable, unique (hash of the whole key), not too long for Windows paths."""
    kw = slug("_".join(k[4])) or "none"
    h = hashlib.sha1(json.dumps(k).encode()).hexdigest()[:8]
    stem = (f"{slug(k[3])}" if k[1] == COMPUTE else f"{k[1]}_{k[2]:02d}_{slug(k[3])}__{kw}")
    return (stem[:72] + "__" + h) if len(stem) > 80 else f"{stem}__{h}"


def folder_of(k, platform):
    return (slug(k[0]) + ("_compute" if k[1] == COMPUTE else "")
            + ("" if platform == "gles3" else f"_{platform}"))
