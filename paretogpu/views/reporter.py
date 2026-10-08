"""What a running command tells the person and the local UI (`python -m paretogpu ui`): one object, Reporter.

  log(text)          a line for the console (the UI shows it in the log of the run)
  phase(id, total)   a step of the command starts (total: units of work, None if unknown)
  step(done)         units done in the current step
  skip(id, note)     a step is not needed this time
  counter(total)     a thread-safe tick() that counts finished units of parallel work
  error(code)        the command stops with a known error (model/errors.py): the UI says what to do about it

With PARETOGPU_PROGRESS=1 in the environment (the UI sets it for the commands it runs) phase / step / skip / error
also print lines `##progress {json}` on stdout; otherwise nothing but the log lines is printed and the console output
stays as it was. The UI knows which steps every command has (features: Command.phases) and shows the ones to come.
"""
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
        """A thread-safe tick() that counts finished units and reports them at most every `every` seconds."""
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


CONSOLE = Reporter()           # the command line: log lines, and the UI's progress lines when it runs the command
QUIET = Reporter(False, None)  # tests and nested work: nothing at all
