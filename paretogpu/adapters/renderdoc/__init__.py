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
import struct
import subprocess
import time
import winreg
import zlib

from paretogpu.adapters.unity import bridge
from paretogpu.model.errors import ParetoError

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = "ParetoGpuRenderDoc.cs"
COUNTERS = os.path.join(HERE, "rd_counters.py")
DRAWS = os.path.join(HERE, "rd_draws.py")
MEMORY = os.path.join(HERE, "rd_memory.py")
DEVICE = os.path.join(HERE, "rd_device.py")
SWEEP = os.path.join(HERE, "rd_sweep.py")
THUMB = os.path.join(HERE, "rd_thumb.py")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
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


def spirv_as():
    """SPIR-V assembler of RenderDoc's plugins (adapters/unity/compiled_text.py)."""
    for find in (qrenderdoc, arm_qrenderdoc):
        try:
            exe = os.path.join(os.path.dirname(find()), "plugins", "spirv", "spirv-as.exe")
        except RenderDocError:
            continue
        if os.path.exists(exe):
            return exe
    raise RenderDocError("spirv-as.exe not found in RenderDoc's plugins/spirv: install RenderDoc")


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
        raise RenderDocError("RenderDoc for Arm GPUs not found: install Arm Performance Studio or set RENDERDOC_ARM_DIR",
                             code="device")
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
    raise RenderDocError("adb not found: install the Android SDK platform tools or set ADB", code="device")


def launch_activity(package, serial=None):
    """-> "package/activity" of the launcher activity."""
    if "/" in package:
        return package
    cmd = [adb()] + (["-s", serial] if serial else []) + ["shell", "cmd", "package", "resolve-activity", "--brief",
                                                         package]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
    act = next((x.strip() for x in reversed(out) if x.strip().startswith(package + "/")), None)
    if not act:
        raise RenderDocError(f"{package} is not installed on the device (or has no launcher activity)", code="device")
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
            raise RenderDocError(f"adb does not see {serial} (it sees: {', '.join(devs) or 'none'})", code="device")
        return serial
    if not devs:
        raise RenderDocError("no Android device: connect it by USB with USB debugging on", code="device")
    if len(devs) > 1:
        raise RenderDocError(f"several devices ({', '.join(devs)}): pass --serial", code="device")
    return devs[0]


def device_gpu(serial):
    """-> malioc core name ("Mali-G78") or None."""
    out = subprocess.run([adb(), "-s", serial, "shell", "dumpsys", "SurfaceFlinger"], capture_output=True, text=True,
                         timeout=60, encoding="utf-8", errors="replace").stdout
    m = re.search(r"(?:GLES: [^,]*, |Name: )(?:Mali|Immortalis)-([A-Z]?\d+)", out)
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


DEVICE_STALL = (("Mali counters", "shader cost:"), 120)  # steps a driver may hang in, seconds without progress


def device_frame(job, out_json, timeout=2400, on_progress=None):
    """job {"serial", "app", "trigger", "wait", "capture", "shaders"} -> rd_device.py answer.
    timeout: seconds counted from the capture on (the app may run as long as the user needs before it). A step of
    DEVICE_STALL silent for its seconds: the server is stopped and the answer written before that step is used."""
    run = _start(DEVICE, out_json, serial=job["serial"], job={**job, "adb": adb()})

    def stalled():
        subprocess.run([adb(), "-s", job["serial"], "shell", "am", "force-stop", "org.renderdoc.renderdoccmd.arm64"],
                       capture_output=True, timeout=30)
        if os.path.exists(out_json + ".partial"):
            os.replace(out_json + ".partial", out_json)
    return _finish(run, timeout, on_progress, waiting=DEVICE_WAITING, stall=(*DEVICE_STALL, stalled))


SWEEP_REPLAY = 6.0  # seconds a shader takes in rd_sweep.py (swap, 3 replays with gaps, unswap)


def device_sweep(serial, capture, gpu, out_json, repeats=4, on_progress=None):
    """Shader cost by Streamline while rd_sweep.py replays the capture on the device -> (its answer, the timeline
    table text of adapters/streamline.finish)."""
    from paretogpu.adapters import streamline
    go = out_json + ".go"
    if os.path.exists(go):
        os.remove(go)
    run = _start(SWEEP, out_json, serial=serial, job={"serial": serial, "capture": capture, "repeats": repeats, "go": go})
    proc = run[0]
    t0, ready = time.time(), None
    while proc.poll() is None and time.time() - t0 < 300 and ready is None:
        try:
            with open(out_json + ".progress", encoding="utf-8") as f:
                text = f.read()
            ready = int(text.split()[1]) if text.startswith("ready ") else None
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.5)
    if ready is None:
        proc.kill()
        _finish(run, 1)  # raises the script's own error when it left one
        raise RenderDocError("the shader sweep did not start on the device", code="device")
    seconds = 120 + (ready + 2) * SWEEP_REPLAY * 1.5
    gator = streamline.start(adb(), serial, "org.renderdoc.renderdoccmd.arm64", gpu, seconds, os.path.dirname(out_json))
    time.sleep(4)
    open(go, "w").close()
    try:
        answer = _finish(run, seconds, on_progress)
    except RenderDocError:
        subprocess.run([adb(), "-s", serial, "shell", "pkill", "-INT", "gatord"], capture_output=True)
        raise
    return answer, streamline.finish(adb(), serial, gator, out_json + ".apc", timeout=seconds + 120)


def _start(script, out_json, capture=None, serial=None, job=None):
    """qrenderdoc --python script; serial: RenderDoc for Arm, the capture replayed on that device (rd_replay.py)."""
    for x in (out_json, out_json + ".error", out_json + ".progress"):
        if os.path.exists(x):
            os.remove(x)
    env = dict(os.environ, PARETOGPU_OUT=out_json, PARETOGPU_LIB=HERE, PARETOGPU_ROOT=ROOT)
    env.pop("PARETOGPU_DEVICE", None)
    if capture:
        env["PARETOGPU_RDC"] = capture
    if job is not None:
        with open(out_json + ".in", "w", encoding="utf-8") as f:
            json.dump(job, f)
        env["PARETOGPU_IN"] = out_json + ".in"
    if serial:  # RenderDoc finds the device through adb
        env.update(PARETOGPU_DEVICE=serial, PATH=os.path.dirname(adb()) + os.pathsep + os.environ.get("PATH", ""))
    log = open(out_json + ".log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([arm_qrenderdoc() if serial else qrenderdoc(), "--python", script], env=env, stdout=log,
                            stderr=subprocess.STDOUT)
    return proc, log, out_json, "device" if serial else "renderdoc"


def _finish(run, timeout, on_progress=None, waiting=None, stall=None):
    """-> the script's answer; RenderDocError on its traceback, a timeout or no answer. waiting: a progress text during
    which the timeout does not run (the user's turn). stall: (step prefixes, seconds, then()) — such a step silent that
    long ends the script, then() runs."""
    proc, log, out_json, code = run
    t0, last, since = time.time(), None, time.time()
    try:
        while proc.poll() is None:
            if time.time() - t0 > timeout:
                raise RenderDocError(f"qrenderdoc did not finish in {timeout} s (last step: {last})", code=code)
            if stall and last and last.startswith(stall[0]) and time.time() - since > stall[1]:
                proc.kill()
                proc.wait()
                stall[2]()
                break
            try:
                with open(out_json + ".progress", encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                text = None
            if text and text != last:
                since = time.time()
                if on_progress:
                    on_progress(text)
            last = text or last
            if waiting and last == waiting:
                t0 = time.time()
            time.sleep(0.5)
    finally:
        if proc.poll() is None:
            proc.kill()
        log.close()
    if os.path.exists(out_json + ".error"):
        with open(out_json + ".error", encoding="utf-8") as f:
            text = f.read().strip()
        last_line = text.splitlines()[-1] if text else ""
        raise RenderDocError(last_line[len("RuntimeError: "):] if last_line.startswith("RuntimeError: ")
                             else text[-2000:], code=code)
    if not os.path.exists(out_json):
        with open(out_json + ".log", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-1500:].strip()
        raise RenderDocError(f"qrenderdoc exited with no answer at step «{last}» (exit code {proc.returncode})"
                             + (f"; its output:\n{tail}" if tail else ""), code=code)
    with open(out_json, encoding="utf-8") as f:
        return json.load(f)


def capture(project, out, timeout=300):
    """Play Mode stays paused: call resume() after the other snapshots. -> {"capture": path, "bytes", "was_paused"}.
    Unity opens RenderDoc's window on the new capture (qrenderdoc --targetcontrol); it is closed: left open it
    connects to the target control port a phone capture forwards over adb."""
    res = bridge.call(project, SCRIPT, "Start", out, "renderdoc", {"pause": True}, RenderDocError, timeout)
    script = ("Get-CimInstance Win32_Process -Filter \"Name='qrenderdoc.exe' AND ParentProcessId="
              f"{int(res['editor_pid'])}\" | Where-Object {{ $_.CommandLine -like '*--targetcontrol*' }} | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60)
    return res


def resume(project, paused):
    bridge.run(project, SCRIPT, "Pause", ["true" if paused else "false"], RenderDocError)


def counters(capture_path, out_json, timeout=900):
    return _finish(_start(COUNTERS, out_json, capture_path), timeout)


def memory(capture_path, out_json, serial=None, timeout=900):
    """-> {"textures": [...], "usage": {texture id: [[event id, usage]]}}."""
    return _finish(_start(MEMORY, out_json, capture_path, serial), timeout)


def thumbnail(capture_path, out_png, size=480, rotation=0, timeout=120):
    """The capture's embedded thumbnail (the presented frame) -> PNG, long side <= size; rotation: the swapchain's
    pre-transform in degrees (rd_device.py), turned back."""
    bmp = out_png + ".bmp"
    for p in (bmp, bmp + ".error"):
        if os.path.exists(p):
            os.remove(p)
    env = dict(os.environ, PARETOGPU_RDC=capture_path, PARETOGPU_OUT=bmp, PARETOGPU_SIZE=str(size))
    subprocess.run([qrenderdoc(), "--python", THUMB], env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(bmp + ".error"):
        with open(bmp + ".error", encoding="utf-8") as f:
            err = f.read()[-2000:]
        os.remove(bmp + ".error")
        raise RenderDocError(err)
    if not os.path.exists(bmp):
        raise RenderDocError("qrenderdoc wrote no thumbnail")
    with open(bmp, "rb") as f:
        data = f.read()
    os.remove(bmp)
    w, h, rows = _bmp_rows(data)
    for _ in range((360 - rotation) % 360 // 90):  # 90° clockwise each
        rows = [bytes(b for y in range(h - 1, -1, -1) for b in rows[y][3 * x:3 * x + 3]) for x in range(w)]
        w, h = h, w
    raw = b"".join(b"\0" + r for r in rows)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    with open(out_png + ".tmp", "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    os.replace(out_png + ".tmp", out_png)
    return out_png


def _bmp_rows(data):
    """24/32-bit uncompressed BMP -> (w, h, RGB rows top to bottom)."""
    off, = struct.unpack_from("<I", data, 10)
    w, h, _, bpp, comp = struct.unpack_from("<iiHHI", data, 18)
    if bpp not in (24, 32) or comp not in (0, 3):
        raise RenderDocError(f"unexpected thumbnail BMP: {bpp} bpp, compression {comp}")
    px, stride = bpp // 8, (w * bpp // 8 + 3) & ~3
    rows = []
    for y in range(abs(h)):
        line = data[off + y * stride:off + y * stride + w * px]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::px], line[1::px], line[0::px]
        rows.append(bytes(rgb))
    if h > 0:
        rows.reverse()
    return w, abs(h), rows


def signature(path):
    st = os.stat(path)
    with open(path, "rb") as f:
        head = hashlib.sha1(f.read(1 << 20)).hexdigest()[:16]
    return f"{st.st_size}|{int(st.st_mtime)}|{head}"


def draws(capture_path, out_json, serial=None, timeout=3600, on_progress=None):
    """rd_draws.py's walk over the capture's draws (post-VS triangles, loop trip counts) into out_json (+ .bin); a
    device capture measures them in rd_device.py, in its replay."""
    return _finish(_start(DRAWS, out_json, capture_path, serial), timeout, on_progress)
