"""Ablation of a shader measured with malioc (plan A3.2): core/ablation.py says what to ablate, this measures it.

  run(src, stage, cores, n, jobs) -> per core: base pipes and price, every statement's inclusive and self cost
Every ablated copy is a keyed rule of the engine (AblateRule): remembered by the hash of the shader's text, so a
shader analysed again is measured only where it changed.
"""
import hashlib

from paretogpu.adapters.gpu import backend
from paretogpu.app.engine import Engine, Rule
from paretogpu.core import loops
from paretogpu.core import pricing as heavy
from paretogpu.core import vertexmove
from paretogpu.core.ablation import ablated, explain, parse, tree
from paretogpu.model.measurement import PIPES
from paretogpu.views.reporter import CONSOLE


def _measure(text, core, stage):
    gpu = backend()
    r = gpu.measure(text, core, "gles", stage)
    if not r["ok"]:
        return {"error": (r.get("error") or "malioc failed").strip().splitlines()[-1][:300]}
    c = heavy.combined(r)
    return {"pipes": c["longest"] if c["longest"] is not None else heavy.fallback(c), "na": c["longest"] is None,
            "regs": max((v["work_regs"] or 0) for v in r["variants"].values())}


def measure(src, core, stage, n, forced_loops):
    """{"pipes", "price", "bound", "regs"} of a GLSL source on a core, dynamic loops priced at n as in the frame
    (core/loops.py: measured at n = 1, 2 and word(1), cycles_at(n)); or {"error"}."""
    if forced_loops:
        texts = [loops.force_glsl(src, n)] if n < 2 else loops.forced(src)[1:]
        if any(t is None for t in texts):
            return {"error": "циклы не удалось прогнать n раз"}
    else:
        texts = [src]
    runs = {}
    for t in texts:
        if t not in runs:
            runs[t] = _measure(t, core, stage)
            if "error" in runs[t]:
                return runs[t]
    ms = [runs[t] for t in texts]
    pipes = ms[0]["pipes"] if len(ms) == 1 else loops.cycles_at({"c": [None] + [m["pipes"] for m in ms]}, n)
    price, bound = heavy.bottleneck(pipes)
    return {"pipes": {k: round(pipes.get(k) or 0.0, 4) for k in PIPES if pipes.get(k) is not None},
            "price": round(price, 4), "bound": bound, "regs": max(m["regs"] for m in ms),
            "na": any(m["na"] for m in ms)}


class AblateRule(Rule):
    """(statement id, core) -> measure() of the shader with that statement ablated, its loops priced at n."""
    phase = "ablation"
    memo = "ablation/2"

    def __init__(self, src, parsed, stage, n, forced):
        self.src, self.p, self.stage, self.n, self.forced = src, parsed, stage, n, forced
        self.sha = hashlib.sha1(src.encode("utf-8")).hexdigest()

    def memo_key(self, key):
        sid, core = key
        return f"{self.sha}|{self.stage}|{sid}|{core}|{self.n}|{self.forced[core]}|{backend().tool}"

    def remember(self, value):
        return "error" not in value

    def build_one(self, key):
        sid, core = key
        return measure(ablated(self.p, sid, self.stage), core, self.stage, self.n, self.forced[core])


def run(src, stage, cores, n=2, jobs=None, rep=CONSOLE, tick=None):
    """Ablation of every statement of main() on every core. Returns
    {"lines": main()'s lines [{"no", "text", "stmt"}], "statements": {id: {...}}, "by_core": {core: {"base", "stmts"}}}."""
    p = parse(src, stage)
    parent, users = tree(p)
    targets = [s for s in p["statements"] if s["ablate"]]
    # does the base need its loops forced (longest path N/A)? the same for every copy
    probe = {c: backend().measure(src, c, "gles", stage) for c in cores}
    forced = {c: bool(probe[c].get("ok")) and heavy.combined(probe[c])["longest"] is None for c in cores}
    base = {c: measure(src, c, stage, n, forced[c]) for c in cores}
    tasks = [(s["id"], c) for s in targets for c in cores if "error" not in base[c]]
    rep.log(f"ablation of {len(targets)} statements of the {stage} shader on {', '.join(cores)} ...")
    tick = tick or rep.counter(len(tasks))  # one counter may span several shaders
    res = Engine(rep, jobs).get(AblateRule(src, p, stage, n, forced), tasks, tick)
    children = {}
    for sid, par in parent.items():
        if par is not None:
            children.setdefault(par, []).append(sid)
    by_core = {}
    for c in cores:
        b = base[c]
        rows = {}
        if "error" not in b:
            for s in targets:
                r = res.get((s["id"], c)) or {"error": "не замерено"}
                if "error" in r:
                    rows[s["id"]] = {"error": r["error"]}
                    continue
                rows[s["id"]] = {"incl": {k: round(b["pipes"].get(k, 0) - r["pipes"].get(k, 0), 4) for k in b["pipes"]},
                                 "price": round(b["price"] - r["price"], 4), "regs": b["regs"] - r["regs"]}
            for sid, row in rows.items():
                if "error" in row:
                    continue
                kids = [rows[k] for k in children.get(sid, []) if "error" not in rows.get(k, {"error": 1})]
                row["self"] = {k: round(max(0.0, v - sum(x["incl"].get(k, 0) for x in kids)), 4)
                               for k, v in row["incl"].items()}
                row["self_price"] = round(max(0.0, row["price"] - sum(x["price"] for x in kids)), 4)
        by_core[c] = {"base": b, "forced": forced[c], "stmts": rows}
    first, last = p["main"]
    by_line = {s["line"]: s["id"] for s in p["statements"]}
    sts = {s["id"]: s for s in p["statements"]}

    def subtree(sid):
        out, todo = [], [sid]
        while todo:
            x = todo.pop()
            out.append(x)
            todo += children.get(x, [])
        return out
    stmts = {}
    for s in p["statements"]:
        if s["type"] != "assign":
            continue
        tree_ids = subtree(s["id"])
        stmts[s["id"]] = {"line": s["line"], "text": s["text"], "ablate": s["ablate"], "loop": s["loop"],
                          "parent": parent.get(s["id"]), "children": children.get(s["id"], []),
                          "used_by_many": len(users.get(s["id"], ())) > 1,
                          "explain": explain([sts[x]["text"] for x in tree_ids], any(sts[x]["loop"] for x in tree_ids), n)}
    moves, budget = [], None
    if stage == "fragment":
        moves = vertexmove.candidates(src)
        budget = vertexmove.budget(src)
    return {"stage": stage, "n": n, "main": [first, last],
            "lines": [{"no": i + 1, "text": p["lines"][i], "stmt": by_line.get(i) if by_line.get(i) in stmts else None}
                      for i in range(first, last + 1)],
            "statements": stmts, "by_core": by_core, "vertex_candidates": moves, "varyings": budget,
            "note": "Цены — GLES-вариант (у Vulkan-варианта Unity нет имён). Максимум по конвейерам не складывается: "
                    "экономия цены у частей не суммируется в цену шейдера."}
