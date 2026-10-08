"""Machine-readable progress for the local UI (`python -m paretogpu ui`).

With PARETOGPU_PROGRESS=1 in the environment (the UI sets it for the commands it runs) the long steps print
lines `##progress {json}` on stdout; otherwise nothing is printed and the console output stays as it was.
  phase(id, total)  a step of the command starts (total: units of work, None if unknown)
  step(done)        units done in the current step (throttled)
  skip(id, note)    a step is not needed this time
The UI knows which steps every command has (ui/server.py PHASES) and shows the ones still to come.
"""
import json
import os
import sys
import threading
import time

ON = os.environ.get("PARETOGPU_PROGRESS") == "1"
PREFIX = "##progress "
_lock = threading.Lock()


def _emit(**d):
    if not ON:
        return
    with _lock:
        sys.stdout.write(PREFIX + json.dumps(d, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def phase(pid, total=None, note=None):
    _emit(event="phase", id=pid, total=total, note=note)


def step(done, total=None, note=None):
    _emit(event="step", done=done, total=total, note=note)


def skip(pid, note=None):
    _emit(event="skip", id=pid, note=note)


def counter(total, every=0.25):
    """A thread-safe tick() that counts finished units and reports them at most every `every` seconds."""
    state = {"done": 0, "last": 0.0}
    lock = threading.Lock()

    def tick(n=1):
        with lock:
            state["done"] += n
            now = time.time()
            if state["done"] >= total or now - state["last"] >= every:
                state["last"] = now
                step(state["done"], total)
    return tick
