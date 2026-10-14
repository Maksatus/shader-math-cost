"""frame_cost.json "code_version": hash of the pricing files' ASTs without docstrings (comments don't change it,
formulas do); runs of different versions are not comparable."""
import ast
import functools
import hashlib
import os

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ("core/frame_cost.py", "core/pricing.py", "core/loops.py", "core/pixels.py", "core/events.py",
         "core/memory.py", "core/variants.py", "core/device.py", "model/measurement.py", "model/cores.py",
         "model/variant.py", "app/cost.py", "app/variants.py", "app/rules.py", "app/frame_loops.py",
         "app/frame_memory.py", "app/device.py", "adapters/malioc.py", "adapters/gpu.py",
         "adapters/renderdoc/rd_loops.py", "adapters/renderdoc/rd_memory.py")


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return tree


def of(files=FILES, root=PACKAGE):
    h = hashlib.sha1()
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            tree = _strip_docstrings(ast.parse(f.read()))
        h.update(rel.encode() + b"\0" + ast.dump(tree).encode() + b"\0")
    return h.hexdigest()[:12]


@functools.lru_cache(maxsize=1)
def current():
    return of()
