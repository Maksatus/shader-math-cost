"""A dispatch's VariantKey is (compute shader, COMPUTE, 0, kernel, ())."""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import TypedDict

EXT = {"vert": ".vert.spv", "frag": ".frag.spv", "comp": ".comp.spv"}  # Vulkan SPIR-V
STAGE = {"vert": "vertex", "frag": "fragment", "comp": "compute"}
SHORT = {v: k for k, v in STAGE.items()}
COMPUTE = -1  # subshader of a compute kernel key
CODE = "spirv "  # the "keyword" of a variant a device ran: the hash of its code (VariantKey.of)
# ComputeShader.FindKernel / HasKernel and ShaderUtil.CompileComputeShaderVariant from a run_script crash the
# 6000.3.18f1 editor, on built-in and package compute shaders alike
COMPUTE_NOT_COMPILED = "compute kernel not compiled: compiling compute shaders from a script crashes Unity 6000.3"
NOT_FINISHED = "no output: the Unity run did not finish"  # transient: compiled again next time
OUTDATED = "only files of an older version of the shader: not used"
STOPPED = "the Unity run stopped at this variant (the editor crashed or the connection broke)"
STOPPED_TWICE = "two Unity runs stopped at this variant: not compiled again until the shader changes (--retry-failed)"


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
        """A device-frame event (ev["code"]) -> identify()'s project variant if known, else its code ("spirv <hash>" in
        place of keywords)."""
        if (ev.get("code") or {}).get("variant"):
            return cls.from_list(ev["code"]["variant"])
        code = (f"{CODE}{ev['code']['hash']}",) if ev.get("code") else None
        if ev["kind"] == "compute":
            c = ev.get("compute") or {}
            return cls(c.get("shader") or "", COMPUTE, 0, c.get("kernel") or "", code or ())
        return cls(ev["shader"] or "", ev["subshader"] or 0, ev["pass_index"] or 0, ev["pass"] or "",
                   code or tuple(sorted(ev["keywords"])))


    @classmethod
    def from_list(cls, x):
        return cls(x[0], x[1], x[2], x[3], tuple(x[4]))

    def to_list(self):
        return [self.shader, self.subshader, self.pass_index, self.pass_name, list(self.keywords)]

    @property
    def is_compute(self):
        return self.subshader == COMPUTE

    @property
    def stages(self):
        return ("comp",) if self.is_compute else ("vert", "frag")

    def __str__(self):
        if self.is_compute:
            return f"{self.shader} | kernel {self.pass_name}"
        return f"{self.shader} | {self.subshader}.{self.pass_index} {self.pass_name} | {' '.join(self.keywords) or '-'}"

    @property
    def base_name(self):
        """Unique (hash of the whole key), short enough for Windows paths."""
        kw = slug("_".join(self.keywords)) or "none"
        h = hashlib.sha1(json.dumps(self.to_list()).encode()).hexdigest()[:8]
        stem = (f"{slug(self.pass_name)}" if self.is_compute
                else f"{self.subshader}_{self.pass_index:02d}_{slug(self.pass_name)}__{kw}")
        return (stem[:72] + "__" + h) if len(stem) > 80 else f"{stem}__{h}"

    @property
    def folder(self):
        return slug(self.shader) + ("_compute" if self.is_compute else "")

    def as_event(self):
        """Materials have variants but no frame events."""
        return {"index": -1, "kind": "draw", "shader": self.shader, "subshader": self.subshader,
                "pass_index": self.pass_index, "pass": self.pass_name, "keywords": list(self.keywords)}


class VariantState(TypedDict, total=False):
    keys: list            # [VariantKey]
    files: dict           # {VariantKey: {short stage: file relative to the variants folder}}
    errors: dict          # {VariantKey: [error]}
    measurements: dict    # {file: {core: Measurement}} (model/measurement.py)
    checked: bool         # the files were checked against the shaders in the open editor
    captured: set         # the variants whose files are the code a device ran (app/variants.place_captured)
    loops: dict           # {file: {core: LoopProfile}} of the files with dynamic loops (app/variants.loops_of)
