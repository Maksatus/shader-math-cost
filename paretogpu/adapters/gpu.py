"""GPU backends: rules and commands measure through backend(), never a tool directly.
Backend.tool (tool path+version) is part of measurement cache keys; measure() returns {"ok": False, "error"} on failure.
New backend: a Backend subclass that calls register() on import, plus its module in KNOWN."""
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
