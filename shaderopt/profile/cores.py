"""Target cores (decision D-04): presets and parsing of --cores."""
from shaderopt import mali

PRESETS = {
    # Bifrost low, Valhall mid, Valhall high (Galaxy S21+ on hand), Valhall new, 5th Gen
    "mobile": ["Mali-G52", "Mali-G57", "Mali-G78", "Mali-G715", "Mali-G720"],
}


def parse(spec):
    """'preset:mobile' or 'Mali-G78,Mali-G52' -> [core]; raises ValueError on unknown names."""
    names = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        if part.startswith("preset:"):
            key = part[len("preset:"):]
            if key not in PRESETS:
                raise ValueError(f"unknown preset '{key}' (known: {', '.join(PRESETS)})")
            names += PRESETS[key]
        else:
            names.append(part)
    if not names:
        raise ValueError(f"no cores in '{spec}'")
    known = {c for c, _, _ in mali.list_cores()}
    if not known:
        raise ValueError(f"malioc ({mali.MALIOC}) listed no cores: is it Mali Offline Compiler?")
    bad = [n for n in names if n not in known]
    if bad:
        raise ValueError(f"unknown core {', '.join(bad)}; malioc knows: {', '.join(sorted(known))}")
    return list(dict.fromkeys(names))
