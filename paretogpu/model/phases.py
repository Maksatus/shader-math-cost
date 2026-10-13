"""The steps a command goes through (views/reporter.phase, app/engine.Rule.phase, features: Command.phases), declared
once: the id every step reports as, its name for the UI and the unit its progress counts (None: no count).
tests/test_phases.py checks that every id the code reports is declared here.
"""

PHASES = {
    "rd_capture": ("Захват кадра в RenderDoc", None),
    "snapshot": ("Снимок кадра (Frame Debugger)", "событий"),
    "rd_counters": ("Подсчёт пикселей (RenderDoc)", None),
    "materials": ("Материалы проекта", None),
    "fingerprints": ("Проверка изменений шейдеров", None),
    "compile": ("Компиляция шейдеров в Unity", "вариантов"),
    "measure": ("Замеры malioc", "замеров"),
    "loops": ("Циклы в шейдерах (malioc)", "замеров"),
    "bounds": ("Границы циклов (GLES)", None),
    "rd_loops": ("Повторы циклов (RenderDoc)", None),
    "rd_memory": ("Память по проходам (RenderDoc)", None),
    "ablation": ("Разбор по строкам (malioc)", "замеров"),
    "report": ("Расчёт и отчёт", None),
    "unity_export": ("Экспорт шейдеров из Unity", None),
    "bench_compile": ("Замеры malioc", "шейдеров"),
    "site": ("Сборка сайта", None),
    "run": ("Выполнение", None),
}


def labels():
    """{id: {"label", "unit"}} for the UI (/api/schema)."""
    return {k: {"label": v[0], "unit": v[1]} for k, v in PHASES.items()}
