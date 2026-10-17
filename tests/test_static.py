"""Every paretogpu module imports, uses no undefined global and has no unused imports."""
import ast
import builtins
import dis
import importlib
import os
import pkgutil
import types
import unittest

import paretogpu

SKIP = ("rd_counters", "rd_draws", "rd_replay", "rd_vulkan", "rd_memory", "rd_device", "rd_sweep", "rd_thumb", "__main__")


def _codes(co):
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from _codes(c)


def _modules():
    for m in pkgutil.walk_packages(paretogpu.__path__, "paretogpu."):
        if not m.name.endswith(SKIP) and ".out." not in m.name:
            yield m.name


class StaticTest(unittest.TestCase):
    def test_modules(self):
        problems = []
        for name in _modules():
            mod = importlib.import_module(name)
            with open(mod.__file__, encoding="utf-8") as f:
                src = f.read()
            for co in _codes(compile(src, mod.__file__, "exec")):
                for ins in dis.get_instructions(co):
                    if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME") and ins.argval not in mod.__dict__ \
                            and not hasattr(builtins, ins.argval):
                        problems.append(f"{name}: undefined {ins.argval} in {co.co_name}")
            tree = ast.parse(src)
            imported = {}
            for node in tree.body:
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imported[(a.asname or a.name).split(".")[0]] = node.lineno
                elif isinstance(node, ast.ImportFrom):
                    for a in node.names:
                        imported[a.asname or a.name] = node.lineno
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            for n, line in imported.items():
                exported = getattr(mod, "__all__", ())
                if n not in used and n not in exported and os.path.basename(mod.__file__) != "__init__.py":
                    problems.append(f"{name}:{line}: unused import {n}")
        self.assertEqual(sorted(set(problems)), [])


if __name__ == "__main__":
    unittest.main()
