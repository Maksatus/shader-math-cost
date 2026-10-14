"""With PARETOGPU_PROGRESS=1 (set by ui/runner.py) phase/step/skip/error also print `##progress {json}` lines on
stdout, {"event": "phase"|"step"|"skip"|"error", id, total, done, note, code}; ui/runner.py parses them.
Phase ids should be those Command.phases lists: the UI shows them in advance."""
import json
import os
import sys
import threading
import time

PREFIX = "##progress "


class Reporter:
    def __init__(self, machine=None, log=print):
        self.machine = os.environ.get("PARETOGPU_PROGRESS") == "1" if machine is None else machine
        self._log = log
        self._lock = threading.Lock()

    def log(self, text):
        if self._log:
            self._log(text)

    def _emit(self, **d):
        if not self.machine:
            return
        with self._lock:
            sys.stdout.write(PREFIX + json.dumps(d, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def phase(self, pid, total=None, note=None):
        self._emit(event="phase", id=pid, total=total, note=note)

    def step(self, done, total=None, note=None):
        self._emit(event="step", done=done, total=total, note=note)

    def skip(self, pid, note=None):
        self._emit(event="skip", id=pid, note=note)

    def error(self, code):
        self._emit(event="error", code=code)

    def counter(self, total, every=0.25):
        """Thread-safe tick(n=1); emits step at most every `every` s (and at total)."""
        state = {"done": 0, "last": 0.0}
        lock = threading.Lock()

        def tick(n=1):
            with lock:
                state["done"] += n
                now = time.time()
                if state["done"] >= total or now - state["last"] >= every:
                    state["last"] = now
                    self.step(state["done"], total)
        return tick


CONSOLE = Reporter()
QUIET = Reporter(False, None)  # tests and nested work
