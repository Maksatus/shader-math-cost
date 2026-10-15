# Runs inside qrenderdoc --python (RenderDoc's UI python, not the normal interpreter).
# In: PARETOGPU_RDC, PARETOGPU_DEVICE (rd_replay.py). Out: PARETOGPU_OUT {"textures": [...], "usage": {texture id:
# [[event id, GetUsage name]]}}; a traceback goes to <out>.error.
import json
import os
import sys
import traceback

out_path = os.environ["PARETOGPU_OUT"]


def main():
    sys.path.insert(0, os.environ["PARETOGPU_LIB"])
    import rd_replay
    rdc, serial = rd_replay.environment()
    rp = rd_replay.Replay(rdc, serial)
    textures, usage = rd_replay.textures(rp.c, bool(serial))
    rp.close()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"textures": textures, "usage": usage}, f)


try:
    main()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
os._exit(0)
