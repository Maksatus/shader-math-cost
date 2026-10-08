"""A shader variant: what a draw or dispatch of a frame ran, and how its compiled files are named.

VariantKey(shader, subshader, pass_index, pass_name, keywords) of a draw; (compute shader, COMPUTE, 0, kernel, ())
of a dispatch. The compiled files of a variant live in <variants>/<shader>/ (GLSL for GLES3) and
<variants>/<shader>_vulkan/ (SPIR-V), named by base_name: readable, unique, short enough for Windows paths.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import TypedDict

EXT = {("gles3", "vert"): ".vert", ("gles3", "frag"): ".frag", ("gles3", "comp"): ".comp",
       ("vulkan", "vert"): ".vert.spv", ("vulkan", "frag"): ".frag.spv", ("vulkan", "comp"): ".comp.spv"}
API = {"gles3": "gles", "vulkan": "vulkan"}  # export platform -> malioc API
PLATFORM = {v: k for k, v in API.items()}  # malioc API -> export platform
STAGE = {"vert": "vertex", "frag": "fragment", "comp": "compute"}
SHORT = {v: k for k, v in STAGE.items()}
COMPUTE = -1  # subshader of a compute kernel key
# ComputeShader.FindKernel / HasKernel and ShaderUtil.CompileComputeShaderVariant called from a run_script crashed
# the 6000.3.18f1 editor three times (2026-10-06), on built-in and package compute shaders alike
COMPUTE_NOT_COMPILED = "compute kernel not compiled: compiling compute shaders from a script crashes Unity 6000.3"
NOT_FINISHED = "no output: the Unity run did not finish"  # transient: compiled again next time


def slug(s):
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_") or "none"


@dataclass(frozen=True, order=True)
class VariantKey:
    shader: str
    subshader: int
    pass_index: int
    pass_name: str
    keywords: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "keywords", tuple(self.keywords))

    @classmethod
    def of(cls, ev):
        """The variant a draw or dispatch event ran."""
        if ev["kind"] == "compute":
            c = ev.get("compute") or {}
            return cls(c.get("shader") or "", COMPUTE, 0, c.get("kernel") or "", ())
        return cls(ev["shader"], ev["subshader"] or 0, ev["pass_index"] or 0, ev["pass"] or "",
                   tuple(sorted(ev["keywords"])))

    @classmethod
    def from_list(cls, x):
        """From its JSON form [shader, subshader, pass index, pass name, [keywords]]."""
        return cls(x[0], x[1], x[2], x[3], tuple(x[4]))

    def to_list(self):
        return [self.shader, self.subshader, self.pass_index, self.pass_name, list(self.keywords)]

    @property
    def is_compute(self):
        return self.subshader == COMPUTE

    @property
    def stages(self):
        """Short stage names of its files: ("vert", "frag"), or ("comp",) for a kernel."""
        return ("comp",) if self.is_compute else ("vert", "frag")

    def __str__(self):
        if self.is_compute:
            return f"{self.shader} | kernel {self.pass_name}"
        return f"{self.shader} | {self.subshader}.{self.pass_index} {self.pass_name} | {' '.join(self.keywords) or '-'}"

    @property
    def base_name(self):
        """File name stem: readable, unique (hash of the whole key), not too long for Windows paths."""
        kw = slug("_".join(self.keywords)) or "none"
        h = hashlib.sha1(json.dumps(self.to_list()).encode()).hexdigest()[:8]
        stem = (f"{slug(self.pass_name)}" if self.is_compute
                else f"{self.subshader}_{self.pass_index:02d}_{slug(self.pass_name)}__{kw}")
        return (stem[:72] + "__" + h) if len(stem) > 80 else f"{stem}__{h}"

    def folder(self, platform):
        """Folder of its files of one platform, in the variants folder."""
        return (slug(self.shader) + ("_compute" if self.is_compute else "")
                + ("" if platform == "gles3" else f"_{platform}"))

    def as_event(self):
        """A draw event that names this variant (materials have variants but no frame events)."""
        return {"index": -1, "kind": "draw", "shader": self.shader, "subshader": self.subshader,
                "pass_index": self.pass_index, "pass": self.pass_name, "keywords": list(self.keywords)}


class VariantState(TypedDict, total=False):
    """The compiled and measured variants of a frame or of materials (app/variants.run)."""
    keys: list            # [VariantKey]
    files: dict           # {VariantKey: {(platform, short stage): file relative to the variants folder}}
    errors: dict          # {VariantKey: [error]}
    measurements: dict    # {file: {core: Measurement}} (model/measurement.py)
    checked: bool         # the files were checked against the shaders in the open editor
    loops: dict           # {file: {core: LoopProfile}} of the files with dynamic loops (app/variants.loops_of)
