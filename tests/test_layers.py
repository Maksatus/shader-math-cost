"""The layers of paretogpu import only downwards (README, "Как устроен код"):

  cli, ui, doctor, corpus  ->  features  ->  app  ->  adapters | store | views  ->  core  ->  model

A module may import its own layer and the layers below it; adapters, store and views do not import each other.
Imports inside functions count too.

Run: python -m unittest discover tests   (from the repository root)
"""
import ast
import os
import unittest

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "paretogpu")
LAYERS = [
    ("interface", ("paretogpu.cli", "paretogpu.__main__", "paretogpu.ui", "paretogpu.doctor", "paretogpu.corpus")),
    ("features", ("paretogpu.features",)),
    ("app", ("paretogpu.app",)),
    ("infra", ("paretogpu.adapters", "paretogpu.store", "paretogpu.views")),
    ("core", ("paretogpu.core",)),
    ("model", ("paretogpu.model",)),
]
SIBLINGS = ("paretogpu.adapters", "paretogpu.store", "paretogpu.views")
SKIP = ("out", "real", "__pycache__")


def layer_of(module):
    for i, (_, prefixes) in enumerate(LAYERS):
        for p in prefixes:
            if module == p or module.startswith(p + "."):
                return i, p
    return None, None


def modules():
    for d, dirs, files in os.walk(PKG):
        dirs[:] = [x for x in dirs if x not in SKIP]
        for fn in files:
            if fn.endswith(".py"):
                path = os.path.join(d, fn)
                rel = os.path.relpath(path, os.path.join(PKG, "..")).replace(os.sep, ".")[:-3]
                yield rel[:-len(".__init__")] if rel.endswith(".__init__") else rel, path


def imports(path, module):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    package = module if path.endswith("__init__.py") else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
            for a in node.names:
                yield f"{base}.{a.name}" if base else a.name, node.lineno


class LayersTest(unittest.TestCase):
    def test_imports_go_downwards(self):
        bad = []
        for module, path in modules():
            if module.endswith("rd_counters"):
                continue
            mine, my_pkg = layer_of(module)
            for target, line in imports(path, module):
                if not target.startswith("paretogpu.") or target in ("paretogpu",):
                    continue
                theirs, their_pkg = layer_of(target)
                if theirs is None:
                    continue
                if mine is None:
                    bad.append(f"{module}:{line} is in no layer")
                elif theirs < mine:
                    bad.append(f"{module}:{line} imports {target} from the layer above ({LAYERS[theirs][0]})")
                elif my_pkg in SIBLINGS and their_pkg in SIBLINGS and my_pkg != their_pkg:
                    bad.append(f"{module}:{line} imports {target}: {my_pkg} and {their_pkg} are independent")
        self.assertEqual(bad, [])

    def test_every_module_has_a_layer(self):
        self.assertEqual([m for m, _ in modules() if m != "paretogpu" and layer_of(m)[0] is None], [])


if __name__ == "__main__":
    unittest.main()
