"""id -> (UI name, progress unit or None); every phase id the code reports must be declared (tests/test_phases.py)."""

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
    "rd_start": ("Запуск игры на телефоне", None),
    "rd_wait": ("Игра на телефоне: ждёт «Снять кадр»", None),
    "rd_device": ("Кадр на устройстве (RenderDoc for Arm)", None),
    "ablation": ("Разбор по строкам (malioc)", "замеров"),
    "report": ("Расчёт и отчёт", None),
    "bench_compile": ("Замеры malioc", "шейдеров"),
    "site": ("Сборка сайта", None),
    "run": ("Выполнение", None),
}


def labels():
    return {k: {"label": v[0], "unit": v[1]} for k, v in PHASES.items()}
