"""What the commands share: the cores and jobs arguments, and how a command stops."""
import argparse
import sys

from paretogpu.adapters import malioc
from paretogpu.core.pricing import FP16_THRESHOLD
from paretogpu.features.spec import Arg, Group
from paretogpu.model.cores import MAIN_CORE, PRESETS
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE as rep


def positive_int(v):
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {v}")
    return n


def fail(message, e=None, code=None):
    """Stop the command: the message for the console, the error code (model/errors.py) for the UI."""
    rep.error(code or getattr(e, "code", "error"))
    sys.exit(message)


def parse_cores(args):
    try:
        return malioc.parse_cores(args.core or args.cores)
    except (ValueError, malioc.MaliocError) as e:
        fail(str(e), e)


def default_variants(values):
    """The UI's values with the project's variants folder when none is given: a variant is compiled once and priced
    in every frame of the project."""
    if not values.get("variants"):
        project = values.get("project") or workspace.snapshot_project(values.get("frame"))
        if project:
            values["variants"] = workspace.variants_dir(project)
    return values


def cores_args(default="preset:mobile", help=None):
    return Group(Arg("--cores", default=default,
                     help=help or f"comma separated cores and/or presets (default {default})"),
                 Arg("--core", help="one core (same as --cores <core>)"))


MAIN_CORES = cores_args(MAIN_CORE, "comma separated cores and/or presets; preset:mobile = " + ",".join(PRESETS["mobile"])
                        + f" (default {MAIN_CORE})")
JOBS = Arg("--jobs", type=positive_int, help="parallel malioc runs (default: CPU count)")
FP16 = Arg("--fp16-threshold", type=float, default=FP16_THRESHOLD,
           help=f"low_fp16 flag below this percent (default {FP16_THRESHOLD})")
