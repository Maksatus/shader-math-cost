import json
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import memory
from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

MEMO = "rd_memory/1"
CAPTURE = "frame.rdc"
ACTIONS = "rd_actions.json"
MEMORY = "rd_memory.json"


def read(frame_dir, rep=CONSOLE, memo=None):
    """-> {"textures", "usage", "actions"} or None. A device frame's capture replays only on the phone, so its kept
    rd_memory.json is read instead."""
    capture, actions = os.path.join(frame_dir, CAPTURE), os.path.join(frame_dir, ACTIONS)
    kept = os.path.join(frame_dir, MEMORY)
    if not os.path.exists(capture) and os.path.exists(kept) and os.path.exists(actions):
        rep.phase("rd_memory")
        with open(kept, encoding="utf-8") as f, open(actions, encoding="utf-8") as g:
            return {**json.load(f), "actions": json.load(g)["actions"]}
    if not os.path.exists(capture) or not os.path.exists(actions):
        rep.skip("rd_memory")
        return None
    rep.phase("rd_memory")
    memo = memo or memo_store.default()
    sig = rdoc.signature(capture)
    raw = memo.get(MEMO, sig)
    if raw is None:
        rep.log("reading the render targets and textures of the capture in RenderDoc ...")
        raw = rdoc.memory(capture, os.path.join(frame_dir, MEMORY))
        memo.put_many(MEMO, [(sig, raw)])
    with open(actions, encoding="utf-8") as f:
        return {**raw, "actions": json.load(f)["actions"]}


def passes(capture, events, rows, cores, rep=CONSOLE):
    if not capture:
        return None
    res = memory.passes(capture["textures"], capture["usage"], capture["actions"], events, rows, cores)
    if res:
        rep.log(f"  {len(res['passes'])} passes, {res['totals']['bytes']['total'] / 2 ** 20:.1f} MB of memory traffic")
    return res
