"""Memory traffic of the frame's passes (plan K2.3): the textures of the snapshot's RenderDoc capture and how every
call used them (adapters/renderdoc/rd_memory.py, remembered per capture in store/memo.py), priced by core/memory.py.

  read(frame_dir) -> what the capture says (textures, usage, actions), or None without a capture
  passes(capture, events, rows, cores) -> core/memory.passes() result, or None
"""
import json
import os

from paretogpu.adapters import renderdoc as rdoc
from paretogpu.core import memory
from paretogpu.store import memo as memo_store
from paretogpu.views.reporter import CONSOLE

MEMO = "rd_memory/1"
CAPTURE = "frame.rdc"
ACTIONS = "rd_actions.json"


def read(frame_dir, rep=CONSOLE, memo=None):
    """{"textures", "usage", "actions"} of the snapshot's capture, or None (no capture)."""
    capture, actions = os.path.join(frame_dir, CAPTURE), os.path.join(frame_dir, ACTIONS)
    if not os.path.exists(capture) or not os.path.exists(actions):
        rep.skip("rd_memory")
        return None
    rep.phase("rd_memory")
    memo = memo or memo_store.default()
    sig = rdoc.signature(capture)
    raw = memo.get(MEMO, sig)
    if raw is None:
        rep.log("reading the render targets and textures of the capture in RenderDoc ...")
        raw = rdoc.memory(capture, os.path.join(frame_dir, "rd_memory.json"))
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
