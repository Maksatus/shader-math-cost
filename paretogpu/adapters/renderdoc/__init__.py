"""RenderDoc. capture(): ParetoGpuRenderDoc.cs pauses Play Mode and puts the Frame Debugger on its last event, which
re-renders the game frame inside the Game view repaint; RenderDoc records that repaint, the same frame the Frame
Debugger snapshot reads right after. rd_*.py run inside qrenderdoc --python (rd_device.py: RenderDoc for Arm GPUs).
Script protocol: env PARETOGPU_RDC (capture), PARETOGPU_IN (job JSON), PARETOGPU_OUT (answer JSON); a failure writes
<out>.error, progress text goes to <out>.progress."""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import winreg

from paretogpu.adapters.unity import bridge
from paretogpu.model.errors import ParetoError

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "ParetoGpuRenderDoc.cs"
COUNTERS = os.path.join(HERE, "rd_counters.py")
LOOPS = os.path.join(HERE, "rd_loops.py")
MEMORY = os.path.join(HERE, "rd_memory.py")
DEVICE = os.path.join(HERE, "rd_device.py")
DEVICE_WAITING = "the app is running: waiting for the capture"


class RenderDocError(ParetoError):
    code = "renderdoc"


def qrenderdoc():
    """Prefers the registered install over Program Files: it is the one Unity loads."""
    cands = []
    if os.environ.get("RENDERDOC_DIR"):
        cands.append(os.path.join(os.environ["RENDERDOC_DIR"], "qrenderdoc.exe"))
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Classes\RenderDoc.RDCCapture.1\DefaultIcon") as k:
            cands.append(os.path.expandvars(winreg.QueryValueEx(k, "")[0]).split(",")[0].strip('"'))
    except OSError:
        pass
    cands += [r"C:\Program Files\RenderDoc\qrenderdoc.exe", shutil.which("qrenderdoc") or ""]
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise RenderDocError("qrenderdoc.exe not found: install RenderDoc or set RENDERDOC_DIR")


def arm_qrenderdoc():
    if os.environ.get("RENDERDOC_ARM_DIR"):
        exe = os.path.join(os.environ["RENDERDOC_ARM_DIR"], "qrenderdoc.exe")
        if os.path.exists(exe):
            return exe
    found = []
    for d in glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Arm", "Arm Performance Studio *")):
        exe = os.path.join(d, "renderdoc_for_arm_gpus", "qrenderdoc.exe")
        m = re.search(r"(\d+(?:\.\d+)*)$", d)
        if m and os.path.exists(exe):
            found.append((tuple(int(x) for x in m.group(1).split(".")), exe))
    if not found:
        raise RenderDocError("RenderDoc for Arm GPUs not found: install Arm Performance Studio or set RENDERDOC_ARM_DIR")
    return max(found)[1]


def adb():
    cands = [os.environ.get("ADB") or "", shutil.which("adb") or ""]
    for v in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        if os.environ.get(v):
            cands.append(os.path.join(os.environ[v], "platform-tools", "adb.exe"))
    cands += sorted(glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Unity", "Hub", "Editor", "*",
                                           "Editor", "Data", "PlaybackEngines", "AndroidPlayer", "SDK", "platform-tools",
                                           "adb.exe")), reverse=True)
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise RenderDocError("adb not found: install the Android SDK platform tools or set ADB")


def launch_activity(package, serial=None):
    """-> "package/activity" of the launcher activity."""
    if "/" in package:
        return package
    cmd = [adb()] + (["-s", serial] if serial else []) + ["shell", "cmd", "package", "resolve-activity", "--brief",
                                                         package]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
    act = next((x.strip() for x in reversed(out) if x.strip().startswith(package + "/")), None)
    if not act:
        raise RenderDocError(f"{package} is not installed on the device (or has no launcher activity)")
    return act


def devices():
    """-> [(serial, model)]; [] without adb."""
    try:
        out = subprocess.run([adb(), "devices", "-l"], capture_output=True, text=True, timeout=20).stdout.splitlines()
    except (RenderDocError, OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            model = next((p[len("model:"):] for p in parts if p.startswith("model:")), parts[0])
            rows.append((parts[0], model.replace("_", " ")))
    return rows


def installed_packages(serial):
    try:
        out = subprocess.run([adb(), "-s", serial, "shell", "pm", "list", "packages", "-3"], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {x.split(":", 1)[1].strip() for x in out.splitlines() if x.startswith("package:")}


def device_serial(serial=None):
    out = subprocess.run([adb(), "devices"], capture_output=True, text=True, timeout=60).stdout.splitlines()
    devs = [x.split()[0] for x in out[1:] if x.strip().endswith("device")]
    if serial:
        if serial not in devs:
            raise RenderDocError(f"adb does not see {serial} (it sees: {', '.join(devs) or 'none'})")
        return serial
    if not devs:
        raise RenderDocError("no Android device: connect it by USB with USB debugging on")
    if len(devs) > 1:
        raise RenderDocError(f"several devices ({', '.join(devs)}): pass --serial")
    return devs[0]


def device_gpu(serial):
    """-> malioc core name ("Mali-G78") or None."""
    out = subprocess.run([adb(), "-s", serial, "shell", "dumpsys", "SurfaceFlinger"], capture_output=True, text=True,
                         timeout=60, encoding="utf-8", errors="replace").stdout
    m = re.search(r"GLES: [^,]*, (?:Mali|Immortalis)-([A-Z]?\d+)", out)
    return f"Mali-{m.group(1)}" if m else None


def device_unlocked(serial):
    """Wakes the screen; False on the lock screen (an app started under it is paused: its frame is not the game's)."""
    base = [adb(), "-s", serial, "shell"]
    run = lambda *a: subprocess.run(base + list(a), capture_output=True, text=True, timeout=60).stdout
    run("input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1)
    awake = "mWakefulness=Awake" in run("dumpsys", "power")
    window = run("dumpsys", "window")
    locked = "isKeyguardShowing=true" in window or "mDreamingLockscreen=true" in window
    return awake and not locked


def device_frame(job, out_json, timeout=2400, on_progress=None):
    """job {"serial", "app", "trigger", "wait", "capture", "shaders"} -> rd_device.py answer.
    timeout: seconds counted from the capture on (the app may run as long as the user needs before it)."""
    for x in (out_json, out_json + ".error", out_json + ".progress"):
        if os.path.exists(x):
            os.remove(x)
    with open(out_json + ".in", "w", encoding="utf-8") as f:
        json.dump({**job, "adb": adb()}, f)
    path = os.environ.get("PATH", "")
    env = dict(os.environ, PARETOGPU_IN=out_json + ".in", PARETOGPU_OUT=out_json, PARETOGPU_LIB=HERE,
               PATH=os.path.dirname(adb()) + os.pathsep + path)  # RenderDoc finds the device through adb
    proc = subprocess.Popen([arm_qrenderdoc(), "--python", DEVICE], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    t0, last = time.time(), None
    try:
        while proc.poll() is None:
            if time.time() - t0 > timeout:
                raise RenderDocError(f"RenderDoc for Arm did not finish in {timeout} s (last step: {last})")
            try:
                with open(out_json + ".progress", encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                text = None
            if text and text != last and on_progress:
                on_progress(text)
            last = text or last
            if last == DEVICE_WAITING:
                t0 = time.time()
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            raise RenderDocError(f.read()[-2000:])
    if not os.path.exists(out_json):
        raise RenderDocError("RenderDoc for Arm wrote no answer (a first-run dialog open? start it once by hand)")
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


def capture(project, out, timeout=300):
    """Play Mode stays paused: call resume() after the other snapshots. -> {"capture": path, "bytes", "was_paused"}."""
    return bridge.call(project, SCRIPT, "Start", out, "renderdoc", {"pause": True}, RenderDocError, timeout)


def resume(project, paused):
    bridge.run(project, SCRIPT, "Pause", ["true" if paused else "false"], RenderDocError)


def _script(script, capture_path, out_json, timeout, what):
    for p in (out_json, out_json + ".error"):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_OUT=out_json)
    subprocess.run([qrenderdoc(), "--python", script], env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            raise RenderDocError(f.read()[-2000:])
    if not os.path.exists(out_json):
        raise RenderDocError(f"qrenderdoc wrote no {what}")
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


def counters(capture_path, out_json, timeout=900):
    return _script(COUNTERS, capture_path, out_json, timeout, "counters")


def memory(capture_path, out_json, timeout=900):
    """-> {"textures": [...], "usage": {texture id: [[event id, usage]]}}."""
    return _script(MEMORY, capture_path, out_json, timeout, "textures")


def signature(path):
    st = os.stat(path)
    with open(path, "rb") as f:
        head = hashlib.sha1(f.read(1 << 20)).hexdigest()[:16]
    return f"{st.st_size}|{int(st.st_mtime)}|{head}"


def free_memory():
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = Status()
    st.dwLength = ctypes.sizeof(Status)
    try:
        return st.ullAvailPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
    except (AttributeError, OSError):
        return None


MAX_WORKERS = 4
PROCESS_MEMORY = 128 << 20  # per qrenderdoc replay: this + 2x the capture size (measured: 252 MB, 102 MB)


def workers_for(capture_path, calls):
    free = free_memory()
    per = PROCESS_MEMORY + 2 * os.path.getsize(capture_path)
    by_memory = max(1, int(free // per)) if free else 1
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 4) // 4, by_memory, calls))


def loop_iterations(capture_path, asked, out_json, timeout=3600, workers=None):
    """asked [{"eid", "pixels"}] -> {eid: {"loops", "nested", "covered", "tried", "samples": [[iterations per outer
    loop]]} or {"error"}}. Calls split in frame order over processes that each replay the capture; RenderDocError only
    if all fail."""
    asked = sorted(asked, key=lambda a: a["eid"])
    k = workers or workers_for(capture_path, len(asked))
    parts = [asked[i * len(asked) // k:(i + 1) * len(asked) // k] for i in range(k)]
    runs = []
    for i, part in enumerate(p for p in parts if p):
        out = f"{out_json}.{i}"
        for x in (out, out + ".error", out + ".progress"):
            if os.path.exists(x):
                os.remove(x)
        with open(out + ".in", "w", encoding="utf-8") as f:
            json.dump(part, f)
        env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_IN=out + ".in", PARETOGPU_OUT=out)
        runs.append((out, subprocess.Popen([qrenderdoc(), "--python", LOOPS], env=env, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)))
    res, errors, t0 = {}, [], time.time()
    try:
        for (out, proc), part in zip(runs, [p for p in parts if p]):
            error = None
            try:
                proc.wait(max(1, timeout - (time.time() - t0)))
            except subprocess.TimeoutExpired:
                error = f"qrenderdoc did not count the loops in {timeout} s"
            if error is None and os.path.exists(out + ".error"):
                with open(out + ".error", encoding="utf-8") as f:
                    error = f.read()[-2000:]
            if error is None and not os.path.exists(out):
                error = "qrenderdoc wrote no loop iterations"
            if error is None:
                with open(out, encoding="utf-8") as f:
                    res.update({int(e): v for e, v in json.load(f).items()})
            else:
                errors.append(error)
                res.update({a["eid"]: {"error": error} for a in part})
    finally:
        for _, proc in runs:
            if proc.poll() is None:
                proc.kill()
    if errors and len(errors) == len(runs):
        raise RenderDocError(errors[0])
    return res
