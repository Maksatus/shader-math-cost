"""Shader cost from a Streamline timeline (adapters/streamline.py) recorded while rd_sweep.py replayed the frame once
per mark: GPU bursts matched to the replays by time, the least burst of each shader's replays against the frame as
captured. -> the knockout of core/device.shader_cost, one pseudo pass "frame" (Streamline sees the whole GPU). Execution
core active includes the replay's own copies of the capture's initial contents (±2-3 M a replay); the unit counters
(instructions, texture filtering, load/store, interpolation) repeat to the unit but for other GPU work in a third of the
replays (_floor)."""
import statistics

IDLE = 0.15    # seconds without GPU activity that end a burst
WINDOW = (-0.1, 0.45)  # a replay's burst starts this long after its mark (the call's latency), before the next mark
PARTIAL = 0.5  # a burst below this share of the median replay is no whole frame
EXEC = "Execution core active cycles"
NAMES = (EXEC, "Arithmetic unit issue cycles", "Load/store unit issue cycles", "Texture unit issue cycles",
         "Varying unit issue cycles", "GPU active cycles")
COLUMNS = {EXEC: ("Execution core active",),
           "Arithmetic unit issue cycles": ("FMA pipe instructions", "CVT pipe instructions", "SFU pipe instructions"),
           "Load/store unit issue cycles": ("Load/Store Unit Cycles:Full reads", "Load/Store Unit Cycles:Partial reads",
                                            "Load/Store Unit Cycles:Full writes",
                                            "Load/Store Unit Cycles:Partial writes"),
           "Texture unit issue cycles": ("Texture Unit Cycles:Filtering active",),
           "Varying unit issue cycles": ("Interpolation requests",),
           "GPU active cycles": ("GPU Cycles:GPU active",)}


def bursts(head, rows):
    """-> [(start seconds, [value per NAMES])] of the GPU's busy stretches."""
    cols = [[i for i, h in enumerate(head) if any(h.endswith(s) for s in COLUMNS[n])] for n in NAMES]
    gpu = cols[NAMES.index("GPU active cycles")]
    out, cur, last = [], None, None
    for r in rows:
        if any(r[i] > 0 for i in gpu):
            if cur is None or r[0] - last > IDLE:
                cur = [r[0], [0.0] * len(NAMES)]
                out.append(cur)
            for k, idx in enumerate(cols):
                cur[1][k] += sum(r[i] for i in idx)
            last = r[0]
    return [tuple(b) for b in out]


def _floor(values):
    # other GPU work (~1/3 of the replays, S21: +0.94 M arithmetic, +0.63 M texture) only adds: the least is the frame
    return [min(v[k] for v in values) for k in range(len(NAMES))]


def knockout(answer, head, rows):
    """answer: rd_sweep.py's {"jobs", "marks"} -> core/device knockout {"names", "base", "spread", "shaders",
    "source"}; {"error"} when the replays cannot be found in the timeline."""
    marks, found = answer["marks"], bursts(head, rows)
    if not marks or not found:
        return {"error": "no_bursts"}
    big = max(b[1][0] for b in found)
    start = next(b[0] for b in found if b[1][0] > 0.2 * big)
    offset = start - marks[0][1]
    seen = {}
    for job, t in marks:
        near = [b for b in found if WINDOW[0] <= b[0] - (t + offset) <= WINDOW[1]]
        if near:
            seen.setdefault(job, []).append(max(near, key=lambda b: b[1][0])[1])
    # some replays run only a sliver of the frame (S21: every third in a row): not the frame, dropped
    whole = PARTIAL * statistics.median(v[0] for vs in seen.values() for v in vs)
    seen = {j: [v for v in vs if v[0] >= whole] for j, vs in seen.items()}
    seen = {j: vs for j, vs in seen.items() if vs}
    base = seen.get(-1)
    if not base or len(base) < 2:
        return {"error": "no_bursts"}
    shaders = []
    for k, j in enumerate(answer["jobs"]):
        row = {key: j[key] for key in ("stage", "mode", "file", "events")}
        if j.get("error"):
            row["error"] = j["error"]
        elif k not in seen:
            row["error"] = "not_seen"
        else:
            row["passes"] = {"frame": _floor(seen[k])}
        shaders.append(row)
    ex = [v[0] for v in base]
    return {"names": list(NAMES), "base": {"frame": _floor(base)}, "spread": {"frame": max(ex) - min(ex)},
            "shaders": shaders, "source": "streamline"}
