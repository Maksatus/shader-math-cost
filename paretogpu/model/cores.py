"""Target cores (decision D-04): presets, the main core (decision D-17), architectures, parsing of --cores."""
MAIN_CORE = "Mali-G78"  # decision D-17: the core shown first and printed
PRESETS = {
    # Bifrost low, Valhall mid, Valhall high (Galaxy S21+ on hand), Valhall new, 5th Gen
    "mobile": ["Mali-G52", "Mali-G57", "Mali-G78", "Mali-G715", "Mali-G720"],
}
ARCHS = ("Bifrost", "Valhall", "Arm 5th Generation")
ARCH_SHORT = {"Bifrost": "Bifrost", "Valhall": "Valhall", "Arm 5th Generation": "5th Gen"}


def main_core(cores):
    """MAIN_CORE if it is among `cores`, else the first one (None for no cores)."""
    return MAIN_CORE if MAIN_CORE in cores else (cores[0] if cores else None)


def parse(spec, known):
    """'preset:mobile' or 'Mali-G78,Mali-G52' -> [core]; raises ValueError on names not in known() (the cores
    malioc lists, asked only once the spec itself is valid)."""
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
    known = set(known())
    bad = [n for n in names if n not in known]
    if bad:
        raise ValueError(f"unknown core {', '.join(bad)}; malioc knows: {', '.join(sorted(known))}")
    return list(dict.fromkeys(names))
