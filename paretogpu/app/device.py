"""Android device frame via RenderDoc for Arm: answer in <snapshot>/device/<time>.json (+ <time>.rdc, SPIR-V in
device/shaders/), built into an ordinary snapshot."""
import json
import os
import sys
import threading
import time

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import device
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE

DIR = "device"
TRIGGER = "capture.now"


def trigger(frame_dir):
    """File whose appearance tells a waiting capture to shoot (UI button / Enter in the console)."""
    return os.path.join(frame_dir, DIR, TRIGGER)


def capture(frame_dir, app, serial=None, wait=None, rep=CONSOLE):
    """Captures when trigger(frame_dir) appears, or after `wait` s; -> path of the answer json."""
    folder = os.path.join(frame_dir, DIR)
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(folder, f"{stamp}.json")
    now = trigger(frame_dir)
    if os.path.exists(now):
        os.remove(now)
    t0 = time.time()
    rep.phase("rd_start")
    serial = rdoc.device_serial(serial)
    if not rdoc.device_unlocked(serial):
        raise rdoc.RenderDocError("the device is locked: unlock it and keep the screen on (Developer options -> Stay "
                                  "awake), then run again", code="device")
    gpu = rdoc.device_gpu(serial)
    activity = rdoc.launch_activity(app, serial)
    rep.log(f"{activity} on {serial} ({gpu or 'GPU unknown'})")
    # the window's subprocess has stdin = NUL, a tty for isatty() on Windows
    console = bool(sys.stdin and sys.stdin.isatty()) and not os.environ.get("PARETOGPU_PROGRESS")
    if wait is None and console:
        threading.Thread(target=_enter, args=(now,), daemon=True).start()
    how = f"the capture is in {wait:g} s" if wait is not None else "press Enter" if console else "press «Снять кадр»"

    waited = []

    def progress(text):
        if text == rdoc.DEVICE_WAITING:
            waited.append(1)
            rep.phase("rd_wait")
            rep.log(f"  the app is running on the device: open the scene you want, then {how}; after the capture do "
                    "not touch the device")
        else:
            if waited:
                rep.phase("rd_device")
            rep.log(f"  {text}")
    answer = rdoc.device_frame({"serial": serial, "app": activity, "wait": wait, "trigger": os.path.abspath(now),
                                "capture": os.path.abspath(os.path.join(folder, f"{stamp}.rdc")),
                                "shaders": os.path.abspath(os.path.join(frame_dir, device.SHADERS))},
                               out, on_progress=progress)
    if not answer.get("draws"):
        raise rdoc.RenderDocError("the device frame has no draws (is the app showing its scene? an older "
                                  "rd_device.py answer?)", code="device")
    answer.update({"app": activity, "gpu": gpu, "captured_at": time.time(),
                   "seconds": round(time.time() - t0, 1)})
    with open(out, "w", encoding="utf-8") as f:
        json.dump(answer, f)
    for x in (out + ".in", out + ".progress", now):
        if os.path.exists(x):
            os.remove(x)
    return out


def _enter(path):
    try:
        input()
    except EOFError:
        return
    open(path, "w").close()


def pre_rotated(answer):
    """Portrait swapchain while the biggest color target is landscape: the game is landscape, the frame pre-rotated."""
    sw = answer.get("screen") or [0, 0]
    rts = [t for t in answer.get("textures") or [] if t.get("color_target") and not t.get("swapchain")]
    big = max(rts, key=lambda t: t["width"] * t["height"], default=None)
    return bool(big) and sw[1] > sw[0] and big["width"] > big["height"]


def build(frame_dir, answer_path, project, rep=CONSOLE):
    """Writes frame_events.json, rd_actions.json, rd_memory.json into frame_dir; -> the snapshot."""
    from paretogpu.app import frame_memory  # circular: it imports this module
    with open(answer_path, encoding="utf-8") as f:
        answer = json.load(f)
    empty = sorted(int(e) for e, d in (answer.get("draws") or {}).items() if not d.get("shaders"))
    if empty:
        raise rdoc.RenderDocError(f"{len(empty)} calls of the device frame have no shaders (first: event {empty[0]}): "
                                  "the replay on the device broke, capture again", code="device")
    snap, actions, mem = device.snapshot(answer, {
        "project": project and os.path.abspath(project), "app": answer.get("app"),
        "gpu": answer.get("gpu"), "captured_at": answer.get("captured_at"), "seconds": answer.get("seconds")})
    for name, doc in ((workspace.SNAPSHOT_FILE, snap), (frame_memory.ACTIONS, actions), (frame_memory.MEMORY, mem)):
        path = os.path.join(frame_dir, name)
        with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, indent=1 if name == workspace.SNAPSHOT_FILE else None, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    if answer.get("capture") and os.path.exists(answer["capture"]):
        from paretogpu.app.frame import save_thumbnail
        save_thumbnail(answer["capture"], frame_dir, rotate=pre_rotated(answer), rep=rep)
    rep.log(f"  snapshot of the device frame: {len(snap['events'])} calls")
    return snap
