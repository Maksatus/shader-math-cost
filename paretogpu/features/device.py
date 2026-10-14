import os

from paretogpu.adapters.renderdoc import RenderDocError
from paretogpu.app import device as device_app
from paretogpu.features.common import fail
from paretogpu.features.frame import print_snapshot
from paretogpu.features.spec import Arg, Command
from paretogpu.store import workspace


def run_command(args):
    if workspace.is_snapshot(args.frame):
        fail(f"{args.frame} already holds a snapshot: give a new folder", code="input")
    os.makedirs(args.frame, exist_ok=True)
    try:
        path = device_app.capture(args.frame, args.app, args.serial, args.wait)
    except RenderDocError as e:
        fail(f"device frame failed: {e}", e)
    print(f"-> {os.path.abspath(path)}")
    project = args.project or workspace.snapshot_project(args.frame)
    print_snapshot(device_app.build(args.frame, path, project), args.frame)
    print(f"price it with `cost {args.frame}`")
    return 0


DEVICE = Command(
    "device", "a frame of an Android device: the development build captured and replayed on the phone (RenderDoc for "
              "Arm GPUs) into a snapshot priced like the editor's",
    [Arg("frame", help="a new folder for the snapshot (`cost` it then)", label="Снимок"),
     Arg("--project", help="the Unity project of the build (the snapshot's project in the lists)", label="Проект"),
     Arg("--app", required=True, help="package of the development build on the device, or package/activity",
         label="Пакет приложения"),
     Arg("--serial", help="adb serial of the device (default: the only one)", label="Устройство (adb serial)"),
     Arg("--wait", type=float, help="capture this many seconds after the start (default: when you press Enter, or the "
                                    "window's «Снять кадр»)", label="Секунд до захвата")],
    run_command, title="Кадр с телефона",
    phases=lambda v: ["rd_start", "rd_wait", "rd_device"])
