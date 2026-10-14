import argparse
from dataclasses import dataclass


@dataclass
class Arg:
    flag: str
    help: str = None
    default: object = None
    type: object = None
    choices: list = None
    action: str = None
    nargs: str = None
    required: bool = False
    metavar: str = None
    label: str = None           # UI form label

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
        """Read by ui/static/js/run_form.js."""
        default = False if self.action == "store_true" and self.default is None else self.default
        if not isinstance(default, (str, int, float, bool)) and default is not None:
            default = None
        return {"dest": self.dest, "flag": None if self.positional else self.flag, "kind": self.kind,
                "multi": self.nargs in ("+", "*"), "required": self.positional or self.required, "default": default,
                "choices": list(self.choices) if self.choices else None,
                "help": (self.help or "").replace("%(default)s", str(self.default)), "metavar": self.metavar,
                "label": self.label, "group": group}


@dataclass
class Group:
    """Mutually exclusive arguments."""
    args: list

    def __init__(self, *args):
        self.args = list(args)


@dataclass
class ResultKind:
    """<name>.json + <name>.html (own_page: the command writes them itself), indexed by <name>.result.json with
    title(doc) and summary(doc) (app/results.py)."""
    name: str
    title: object
    summary: object = lambda doc: {}
    data: str = None
    page: str = None
    own_page: bool = False

    def __post_init__(self):
        self.data = self.data or f"{self.name}.json"
        self.page = self.page or f"{self.name}.html"


@dataclass
class Command:
    name: str
    help: str
    args: list
    run: object                                     # run(args) -> exit code
    phases: object = None                           # phases(values) -> [phase id] (views/reporter.py)
    report: object = None                           # report(values) -> page path the UI opens, or None
    ui_values: object = None                        # ui_values(values) -> values + folders the UI does not ask for
    result: object = None                           # ResultKind
    title: str = None                               # UI name (None: name)

    def flat_args(self):
        """[(Arg, mutually exclusive group index or None)]."""
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
        return {"help": self.help, "title": self.title or self.name, "args": [a.schema(g) for a, g in self.flat_args()]}

    def phases_of(self, values):
        return self.phases(values) if self.phases else []

    def report_of(self, values):
        return self.report(values) if self.report else None

    def ui_values_of(self, values):
        return self.ui_values(dict(values)) if self.ui_values else dict(values)


def build_parser(commands, prog="paretogpu"):
    ap = argparse.ArgumentParser(prog=prog)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in commands:
        c.add_to(sub)
    return ap
