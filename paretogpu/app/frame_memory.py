import json
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import memory
from paretogpu.store import workspace
from paretogpu.views.reporter import CONSOLE

ACTIONS = "rd_actions.json"
MEMORY = "rd_memory.json"


def read(frame_dir, frame, rep=CONSOLE):
    """-> {"textures", "usage", "actions"} or None. rd_memory.json is read once from the capture (a device frame's
    while it is captured) and kept in the snapshot."""
    capture, serial = workspace.capture_of(frame_dir, frame)
    actions, kept = os.path.join(frame_dir, ACTIONS), os.path.join(frame_dir, MEMORY)
    if not os.path.exists(actions) or not (os.path.exists(kept) or capture and os.path.exists(capture)):
        rep.skip("rd_memory")
        return None
    rep.phase("rd_memory")
    if os.path.exists(kept):
        with open(kept, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        rep.log("reading the render targets and textures of the capture in RenderDoc ...")
        raw = rdoc.memory(capture, kept, serial)
    with open(actions, encoding="utf-8") as f:
        return {**raw, "actions": json.load(f)["actions"]}


def passes(capture, events, rows, cores, formats, rep=CONSOLE):
    if not capture:
        return None
    res = memory.passes(capture["textures"], capture["usage"], capture["actions"], events, rows, cores, formats)
    if res:
        rep.log(f"  {len(res['passes'])} passes, {res['totals']['bytes']['total'] / 2 ** 20:.1f} MB of memory traffic")
    return res
