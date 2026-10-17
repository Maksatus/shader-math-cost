"""Arm Streamline (Performance Studio): Mali hardware counters of a debuggable Android process over time, without root:
gatord pushed to the device and run against the package, Streamline-cli turning the capture into a timeline table."""
import csv
import glob
import io
import os
import re
import shutil
import subprocess

REMOTE = "/data/local/tmp"
COUNTERS = ("GPU_ACTIVE", "EXEC_CORE_ACTIVE", "EXEC_INSTR_FMA", "EXEC_INSTR_CVT", "EXEC_INSTR_SFU",
            "TEX_FILT_NUM_OPERATIONS", "LS_MEM_READ_FULL", "LS_MEM_READ_SHORT", "LS_MEM_WRITE_FULL",
            "LS_MEM_WRITE_SHORT", "VARY_INSTR", "FRAG_ACTIVE", "TILER_ACTIVE")


class StreamlineError(RuntimeError):
    code = "device"


def studio():
    found = []
    for d in glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Arm", "Arm Performance Studio *")):
        m = re.search(r"(\d+(?:\.\d+)*)$", d)
        if m and os.path.exists(os.path.join(d, "streamline", "Streamline-cli.exe")):
            found.append((tuple(int(x) for x in m.group(1).split(".")), os.path.join(d, "streamline")))
    if not found:
        raise StreamlineError("Streamline not found: install Arm Performance Studio")
    return max(found)[1]


def config(gpu):
    """Counter configuration for a Mali GPU name such as "Mali-G78"."""
    name = "ARM_" + gpu.replace(" ", "")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<configurations revision="3">\n'
            + "".join(f'  <configuration counter="{name}_{c}"/>\n' for c in COUNTERS) + "</configurations>\n")


def start(adb, serial, package, gpu, seconds, local_dir):
    """Pushes gatord and its configuration, starts a capture of `package` for `seconds` -> the running adb process."""
    gatord = os.path.join(studio(), "bin", "android", "arm64", "gatord")
    xml = os.path.join(local_dir, "paretogpu_sl.xml")
    with open(xml, "w", encoding="utf-8") as f:
        f.write(config(gpu))
    for src, dst in ((gatord, f"{REMOTE}/gatord"), (xml, f"{REMOTE}/paretogpu_sl.xml")):
        r = subprocess.run([adb, "-s", serial, "push", src, dst], capture_output=True, text=True)
        if r.returncode:
            raise StreamlineError(f"adb push {os.path.basename(src)} failed: {r.stderr.strip()[-300:]}")
    subprocess.run([adb, "-s", serial, "shell", "chmod", "755", f"{REMOTE}/gatord", ";", "rm", "-rf",
                    f"{REMOTE}/paretogpu.apc"], capture_output=True)
    return subprocess.Popen([adb, "-s", serial, "shell",
                             f"cd {REMOTE} && ./gatord --config-xml paretogpu_sl.xml --android-pkg {package} "
                             f"--max-duration {int(seconds)} --output paretogpu.apc > paretogpu_gator.log 2>&1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def finish(adb, serial, proc, local_apc, timeout):
    """Stops gatord (SIGINT ends a capture cleanly; else its --max-duration does), pulls the capture -> its timeline
    table text (Streamline-cli -report)."""
    subprocess.run([adb, "-s", serial, "shell", "pkill", "-INT", "gatord"], capture_output=True)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise StreamlineError("gatord did not stop in time") from None
    shutil.rmtree(local_apc, ignore_errors=True)
    r = subprocess.run([adb, "-s", serial, "pull", f"{REMOTE}/paretogpu.apc", local_apc], capture_output=True, text=True)
    if r.returncode or not os.path.isdir(local_apc):
        log = subprocess.run([adb, "-s", serial, "shell", "tail", "-3", f"{REMOTE}/paretogpu_gator.log"],
                             capture_output=True, text=True).stdout.strip()
        raise StreamlineError(f"no Streamline capture on the device: {log[-300:]}")
    r = subprocess.run([os.path.join(studio(), "Streamline-cli.exe"), "-report", local_apc, "-timeline",
                        "-default-template", "basic"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if "(For all processes):" not in r.stdout:
        raise StreamlineError(f"Streamline-cli gave no timeline: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def timeline(text):
    """Streamline-cli -timeline table -> (column names, rows of floats); column 0 is seconds."""
    rows = list(csv.reader(io.StringIO(text.split("(For all processes):", 1)[1].strip())))
    head = rows[0]
    return head, [[float(x.replace(",", ".") or 0) for x in r] for r in rows[1:] if len(r) == len(head)]
