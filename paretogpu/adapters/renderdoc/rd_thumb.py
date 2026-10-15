# Runs inside qrenderdoc --python. In: capture PARETOGPU_RDC, PARETOGPU_SIZE. Out: PARETOGPU_OUT the capture's
# embedded thumbnail as a 24-bit BMP; a traceback goes to <out>.error.
import os
import traceback

import renderdoc as rd

out_path = os.environ["PARETOGPU_OUT"]
try:
    cap = rd.OpenCaptureFile()
    r = cap.OpenFile(os.environ["PARETOGPU_RDC"], "", None)
    if getattr(r, "code", r) != rd.ResultCode.Succeeded:
        raise RuntimeError(f"cannot open capture: {r}")
    thumb = cap.GetThumbnail(rd.FileType.BMP, int(os.environ.get("PARETOGPU_SIZE", "0")))
    if not thumb.data:
        raise RuntimeError("the capture has no thumbnail")
    with open(out_path, "wb") as f:
        f.write(bytes(thumb.data))
    cap.Shutdown()
except Exception:
    with open(out_path + ".error", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
os._exit(0)
