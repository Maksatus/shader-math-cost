"""A command of `python -m paretogpu`, described once: the CLI parser and the UI's forms are made from it.

  Command(name, help, args, run)  run(args) -> exit code; args: [Arg | Group]
  Arg("--cores", default=..., help=...), Arg("folder") for a positional; Group(Arg, Arg): mutually exclusive
  phases(values) -> the progress phases (views/reporter.py) a run goes through, in order, for the UI
  report(values) -> the page the run writes (the UI opens it), or None
  ui_values(values) -> the values with the folders the UI does not ask for filled in
"""
import argparse
from dataclasses import dataclass, field


@dataclass
class Arg:
    flag: str                   # "--cores", or the name of a positional
    help: str = None
    default: object = None
    type: object = None
    choices: list = None
    action: str = None          # "store_true" | "append"
    nargs: str = None
    required: bool = False
    metavar: str = None

    @property
    def positional(self):
        return not self.flag.startswith("-")

    @property
    def dest(self):
        return self.flag.lstrip("-").replace("-", "_")

    @property
    def kind(self):
        if self.positional:
            return "positional"
        return {"store_true": "flag", "append": "list"}.get(self.action, "value")

    def add_to(self, p):
        kw = {k: v for k, v in (("help", self.help), ("type", self.type), ("choices", self.choices),
                                ("action", self.action), ("nargs", self.nargs), ("metavar", self.metavar))
              if v is not None}
        if self.default is not None:
            kw["default"] = self.default
        if self.required:
            kw["required"] = True
        p.add_argument(self.flag, **kw)

    def schema(self, group=None):
        """The UI's description of the argument (ui/app.html builds its form fields from it)."""
        default = False if self.action == "store_true" and self.default is None else self.default
        if not isinstance(default, (str, int, float, bool)) and default is not None:
            default = None
        return {"dest": self.dest, "flag": None if self.positional else self.flag, "kind": self.kind,
                "multi": self.nargs in ("+", "*"), "required": self.positional or self.required, "default": default,
                "choices": list(self.choices) if self.choices else None,
                "help": (self.help or "").replace("%(default)s", str(self.default)), "metavar": self.metavar,
                "group": group}


@dataclass
class Group:
    """Mutually exclusive arguments."""
    args: list

    def __init__(self, *args):
        self.args = list(args)


@dataclass
class Command:
    name: str
    help: str
    args: list
    run: object                                     # run(args) -> exit code
    phases: object = None                           # phases(values) -> [phase id]
    report: object = None                           # report(values) -> path of the page the run writes
    ui_values: object = None                        # ui_values(values) -> values with the UI's own folders
    extra: dict = field(default_factory=dict)

    def flat_args(self):
        """[(Arg, index of its mutually exclusive group or None)] in the order they are declared."""
        out, g = [], 0
        for a in self.args:
            if isinstance(a, Group):
                out += [(x, g) for x in a.args]
                g += 1
            else:
                out.append((a, None))
        return out

    def add_to(self, sub):
        p = sub.add_parser(self.name, help=self.help)
        for a in self.args:
            if isinstance(a, Group):
                grp = p.add_mutually_exclusive_group()
                for x in a.args:
                    x.add_to(grp)
            else:
                a.add_to(p)
        p.set_defaults(func=self.run)
        return p

    def schema(self):
        return {"help": self.help, "args": [a.schema(g) for a, g in self.flat_args()]}

    def phases_of(self, values):
        return self.phases(values) if self.phases else []


def build_parser(commands, prog="paretogpu"):
    ap = argparse.ArgumentParser(prog=prog)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in commands:
        c.add_to(sub)
    return ap
