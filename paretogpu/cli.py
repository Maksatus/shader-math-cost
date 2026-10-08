"""ParetoGPU command line.

  python -m paretogpu measure <folder> [--cores preset:mobile|Mali-G78,...] [--api gles] [--out DIR]
                              [--fp16-threshold 25] [--no-report] [--jobs N]
  python -m paretogpu report <folder with measurements.jsonl> [--out DIR]
  python -m paretogpu export --project <Unity project> <shader> [...] [--out DIR] [--platforms gles3,vulkan]
                             [--mode auto|editor|batch] [--measure] [--cores ...]
  python -m paretogpu cost <frame folder> [--project <Unity>] [--cores preset:mobile]
                           [--main-core Mali-G78] [--api vulkan|gles] [--variants DIR] [--no-compile]
                           [--recompile] [--retry-failed] [--loop-iters 2] [--loop-iters-shader NAME=N ...] [--materials]
  python -m paretogpu compare <A> <B> [--out DIR]   A, B: snapshot folder (its latest cost), frame_cost.json or
                                                   <snapshot>/costs/<time>.json -> compare.html, compare.json
  python -m paretogpu matcompare --project <Unity> <A.mat> <B.mat> [--cores preset:mobile] [--api vulkan|gles]
                                 [--variants DIR] [--out DIR] [--no-compile] [--recompile]
                                 two materials: their variants (keywords, passes) per pixel / vertex at loop n = 0..8
  python -m paretogpu matshader --project <Unity> <material.mat> [--cores ...] [--ablate-cores Mali-G78] [--n 2]
                                one material: its variants and what every line of their code costs (ablation)
  python -m paretogpu hotspots <snapshot folder> [--top 10] [--core Mali-G78]
                               the heaviest shaders of a priced snapshot: their costliest parts and why -> hotspots.html
  python -m paretogpu ui [--port 8765] [--no-window]   local web UI: the function cost site, runs with progress, reports

Every command is declared in its feature module (features/: arguments, what it runs, its phases for the UI).
"""

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
    # names of shaders and objects may have characters the console code page cannot print (cp1251 into a pipe)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
