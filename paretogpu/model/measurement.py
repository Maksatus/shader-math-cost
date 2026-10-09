"""What malioc says about a shader on one core, and the prices made of it."""
from dataclasses import dataclass
from typing import TypedDict

PIPES = ("arith", "fma", "cvt", "sfu", "ls", "v", "t")  # short names of malioc's pipelines


@dataclass(frozen=True)
class PipeModel:
    """The pipes of a GPU family, as core/pricing.py prices them: the busiest pipe on the path is the price; the
    arithmetic pipe `arith` may be broken down into sub-pipes that name the bound; a vertex shader's variants
    (IDVS: position, varying) all run per vertex."""
    pipes: tuple          # in the order bounds are named
    arith: str = None
    arith_sub: tuple = ()
    main_variant: str = "main"


MALI = PipeModel(pipes=("fma", "cvt", "sfu", "ls", "v", "t", "arith"), arith="arith", arith_sub=("fma", "cvt", "sfu"))
Cycles = dict  # {pipe: cycles}


class VariantPerf(TypedDict, total=False):
    """One malioc variant of a shader: "main" (fragment, compute), "position" and "varying" (IDVS vertex)."""
    longest: Cycles   # None when malioc reports N/A (dynamic loops)
    shortest: Cycles
    total: Cycles
    bound: list       # pipes of the longest path's bottleneck
    work_regs: int
    uniform_regs: int
    occupancy: int
    spilling: bool
    spill_bytes: int
    fp16_pct: int


class Measurement(TypedDict, total=False):
    """One shader file on one core (adapters/malioc.measure; measurements.jsonl adds file, root, shader, pass,
    keywords). A failure is {"ok": False, "error", "file", "core", "api"}."""
    ok: bool
    error: str
    file: str
    core: str
    arch: str
    api: str          # gles | vulkan
    stage: str        # vertex | fragment | compute
    driver: str
    malioc: str       # malioc version
    source_sha1: str
    uniform_computation: bool
    variants: dict    # {name: VariantPerf}
    notes: list


class Score(TypedDict):
    """Heaviness of one stage on one core (core/pricing.score): the bottleneck cycles and why."""
    cycles: float
    path: str         # longest | total | "loop n=<n>"
    bound: list
    work_regs: int
    fp16_pct: int
    flags: list       # core/pricing.FLAGS


class LoopProfile(TypedDict):
    """A shader whose dynamic loops are forced to n = 0, 1, 2 iterations (app/variants.parametric)."""
    c: list           # [Cycles at n = 0, 1, 2], longest path
    work_regs: int
    spilling: bool
