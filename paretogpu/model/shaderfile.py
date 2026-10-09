"""Shader files: the stage from the extension (shader.frag, shader.vert.spv), GLSL text or binary SPIR-V."""
import os

STAGES = {".vert": "vertex", ".frag": "fragment", ".comp": "compute"}
EXT = {v: k for k, v in STAGES.items()}
SPIRV_EXT = ".spv"  # binary SPIR-V: shader.frag.spv (Vulkan only)


def stage_of(path):
    """Shader stage from a file name (shader.frag, shader.vert.spv, ...), or None."""
    base = path[:-len(SPIRV_EXT)] if path.endswith(SPIRV_EXT) else path
    return STAGES.get(os.path.splitext(base)[1])


def is_spirv(path):
    return path.endswith(SPIRV_EXT)


def api_of(path, api):
    """The API a file is measured for: binary SPIR-V only for Vulkan, GLSL for `api`."""
    return "vulkan" if is_spirv(path) else api
