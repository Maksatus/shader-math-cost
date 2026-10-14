from dataclasses import dataclass
from typing import TypedDict

PIPES = ("arith", "fma", "cvt", "sfu", "ls", "v", "t")  # short names of malioc's pipelines


@dataclass(frozen=True)
class PipeModel:
    """Price = busiest pipe on the path; `arith` may split into arith_sub pipes that name the bound; all IDVS vertex
    variants (position, varying) run per vertex."""
    pipes: tuple          # in the order bounds are named
    arith: str = None
    arith_sub: tuple = ()
    main_variant: str = "main"


MALI = PipeModel(pipes=("fma", "cvt", "sfu", "ls", "v", "t", "arith"), arith="arith", arith_sub=("fma", "cvt", "sfu"))
Cycles = dict  # {pipe: cycles}


class VariantPerf(TypedDict, total=False):
    """malioc variant: "main" (fragment, compute), "position" / "varying" (IDVS vertex)."""
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
    """One file on one core. A failure is {"ok": False, "error", "file", "core", "api"}."""
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
    cycles: float
    path: str         # longest | total | "loop n=<n>"
    bound: list
    work_regs: int
    fp16_pct: int
    flags: list       # core/pricing.FLAGS


class LoopProfile(TypedDict):
    """Forced dynamic loops measured on the longest path (core/loops.profile)."""
    model: str        # core/loops.MODEL
    base: dict        # Cycles with every outer loop at 1 iteration
    loops: list       # [{"zero", "two", "word"}] Cycles with outer loop k at 0 / 2 / 1 with its nested loops twice
    zero: dict        # Cycles with every outer loop at 0 (two or more loops only)
    work_regs: int
    spilling: bool
