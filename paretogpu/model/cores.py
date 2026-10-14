MAIN_CORE = "Mali-G78"  # the core shown first and printed
PRESETS = {
    # Bifrost low, Valhall mid / high / new, 5th Gen
    "mobile": ["Mali-G52", "Mali-G57", "Mali-G78", "Mali-G715", "Mali-G720"],
}
# roofline device per core (core/memory.py): shader cores, GPU MHz, peak DRAM GB/s; G78 = the Galaxy S21 on hand,
# the others are typical phones (unverified)
DEVICES = {
    "Mali-G52": {"device": "Helio G85 (G52 MC2)", "cores": 2, "mhz": 1000, "gbps": 14.9},
    "Mali-G57": {"device": "Dimensity 700 (G57 MC2)", "cores": 2, "mhz": 950, "gbps": 17.1},
    "Mali-G78": {"device": "Galaxy S21, Exynos 2100 (G78 MP14)", "cores": 14, "mhz": 854, "gbps": 51.2},
    "Mali-G715": {"device": "Dimensity 9200 (Immortalis-G715 MC11)", "cores": 11, "mhz": 981, "gbps": 68.3},
    "Mali-G720": {"device": "Dimensity 9300 (Immortalis-G720 MC12)", "cores": 12, "mhz": 1300, "gbps": 76.8},
}
ARCHS = ("Bifrost", "Valhall", "Arm 5th Generation")
ARCH_SHORT = {"Bifrost": "Bifrost", "Valhall": "Valhall", "Arm 5th Generation": "5th Gen"}


def main_core(cores):
    return MAIN_CORE if MAIN_CORE in cores else (cores[0] if cores else None)


def parse(spec, known):
    """'preset:mobile' or 'Mali-G78,Mali-G52' -> [core]; known() (malioc's list) is called only once the spec parses."""
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
