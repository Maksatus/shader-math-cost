"""GPU backends: how the shaders of one GPU family are measured. The rules of the app (app/rules.py, app/ablation.py)
and the commands ask a Backend, never a tool directly; Mali (adapters/malioc.py, malioc) is the first one.

A backend gives:
  name, pipes          its pipe model (model/measurement.PipeModel): what core/pricing.py prices
  tool                 what its measurements depend on besides the shader (the tool and its version): part of the
                       keys measurements are remembered under
  version()            the tool's version
  cores()              [(core, architecture, [APIs])]
  parse_cores(spec)    --cores ("preset:mobile", "Mali-G78,Mali-G52") -> [core]
  measure(src, core, api, stage) -> Measurement (model/measurement.py), {"ok": False, "error"} on a failure
  spills(rec)          the record spills registers
  Error                the exception of a tool that cannot run

  backend(name=None) -> the backend (PARETOGPU_GPU, default "mali")
To add one: a module of adapters/ with a Backend subclass that calls register() when imported, and its name in KNOWN.
"""
import importlib
import os

from paretogpu.model.errors import ParetoError
from paretogpu.model.shaderfile import is_spirv

KNOWN = {"mali": "paretogpu.adapters.malioc"}
_backends = {}


class Backend:
    name = None
    pipes = None
    tool = None
    Error = ParetoError

    def version(self):
        raise NotImplementedError

    def cores(self):
        raise NotImplementedError

    def parse_cores(self, spec):
        raise NotImplementedError

    def measure(self, src, core, api, stage="fragment"):
        raise NotImplementedError

    def spills(self, rec):
        raise NotImplementedError


def register(b):
    _backends[b.name] = b


def backend(name=None):
    name = name or os.environ.get("PARETOGPU_GPU") or "mali"
    if name not in _backends:
        if name not in KNOWN:
            raise ParetoError(f"no GPU backend {name!r} (known: {', '.join(KNOWN)})", "input")
        importlib.import_module(KNOWN[name])
    return _backends[name]


def read_source(path):
    """GLSL text, or bytes for a binary SPIR-V file."""
    if is_spirv(path):
        with open(path, "rb") as f:
            return f.read()
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()
