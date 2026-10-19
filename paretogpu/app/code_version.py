"""frame_cost.json "code_version": hash of the ASTs of every module the price depends on (DIRS), without docstrings
(comments don't change it, formulas do); runs of different versions are not comparable."""
import ast
import functools
import hashlib
import os

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("core", "model", "app", "adapters")


def files(root=PACKAGE):
    return sorted(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                  for top in DIRS for d, _, names in os.walk(os.path.join(root, top)) for f in names
                  if f.endswith(".py"))


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return tree


def of(rels=None, root=PACKAGE):
    h = hashlib.sha1()
    for rel in rels if rels is not None else files(root):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            tree = _strip_docstrings(ast.parse(f.read()))
        h.update(rel.encode() + b"\0" + ast.dump(tree).encode() + b"\0")
    return h.hexdigest()[:12]


@functools.lru_cache(maxsize=1)
def current():
    return of()
