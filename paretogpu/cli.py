import sys

from paretogpu.features import COMMANDS
from paretogpu.features.spec import Arg, Command, build_parser as _build_parser


def run_ui(args):
    from paretogpu.ui import server
    return server.serve(args.port, open_window=not args.no_window, stay=args.stay)


UI = Command(
    "ui", "local web UI (127.0.0.1): the function cost site, the commands with their progress, the reports of the "
          "snapshots",
    [Arg("--port", type=int, default=8765),
     Arg("--no-window", action="store_true", help="do not open the window (Edge app mode or the browser)"),
     Arg("--stay", action="store_true", help="keep running after the last window is closed")],
    run_ui)


def build_parser():
    return _build_parser(COMMANDS + [UI])


def main(argv=None):
    # shader/object names may not encode in the console code page (cp1251 into a pipe)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
