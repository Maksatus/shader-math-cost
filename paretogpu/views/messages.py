"""core/ and app/ emit warnings as {"code", **params}; rendered here for the console and, via export() embedded as
MSG (views/html.py), in the browser."""

WARNINGS = {
    "no_common_cores": "У расчётов нет общих ядер: сравнить нечего.",
    "cores_differ": "Разные наборы ядер: сравниваются только общие ({cores}).",
    "code_differs": "Расчёты сделаны разными версиями ParetoGPU (A {a}, B {b}): часть разницы может дать сам "
                    "инструмент, а не шейдеры или сцена. Пересчитайте оба снимка («Пересчитать» в списке снимков).",
    "malioc_differs": "Разные версии malioc (A {a}, B {b}): часть разницы даёт компилятор, а не шейдеры.",
    "render_msaa": "Разный MSAA основной цели (A {a}×, B {b}×).",
    "render_color": "Разный формат основной цели (A {a}, B {b}).",
    "render_shadow_maps": "Разные карты теней (A {a}, B {b}): другой профиль качества или каскады.",
    "render_passes": "Разный набор проходов: только в A — {a}; только в B — {b}. Кадры сняты с разными настройками "
                     "рендера (профиль качества, эффекты), их цена несопоставима.",
    "pixel_methods_differ": "Пиксели посчитаны по-разному (A: {a}; B: {b}): «renderdoc» — запуски пиксельного "
                            "шейдера на GPU ПК, «samples» — фрагменты телефона после теста глубины. Часть разницы "
                            "«объём работы» — разница способов, а не сцены.",
    "vertex_methods_differ": "Вершины посчитаны по-разному (A: {a}; B: {b}): «renderdoc» — запуски вершинного шейдера "
                             "на GPU ПК (его кэш вершин меньше, чем у Mali: в среднем +14%, у плотных сеток до +50%), "
                             "«indices» — уникальные вершины draw на телефоне. Часть разницы «объём работы» — разница "
                             "способов, а не сцены.",
    "resolution_differs": "Разное разрешение кадра (A {a}, B {b}): пиксели несопоставимы.",
    "quality_differs": "Разный уровень качества Unity (A {a}, B {b}).",
    "pipeline_differs": "Разный ассет пайплайна (A {a}, B {b}): проходы и эффекты кадров могут отличаться.",
    "scale_differs": "Разный масштаб рендера (A {a}, B {b} от Game view).",
    "projects_differ": "Расчёты из разных проектов.",
    "unpriced_draws": "Draw без цены: в A {a}, в B {b}. Их стоимость не входит в сравнение.",
    "unchecked": "шейдеры не перекомпилировались перед расчётом, цены могут быть устаревшими.",
    "build_target_not_mobile": "Платформа сборки в редакторе — {target}, а не Android: качество, keywords и варианты "
                               "кадра могут быть не те, что на устройстве.",
    "quads_samples_mismatch": "У {n} draw растеризация геометрии разошлась с Samples Passed захвата больше чем на 2% "
                              "(stencil, discard или глубина из другого прохода): их число потоков шейдера неточно. "
                              "Худшие: {worst}.",
    "quads_unread": "Событие #{event}: геометрия не прочитана ({error}), число потоков шейдера взято из счётчика.",
    "quality_not_android": "Уровень качества в редакторе — {quality}, а сборка Android использует только {android}: "
                           "тени, свет и проходы кадра не те, что на телефоне.",
    "rd_count_mismatch": "У {n} draw вызовы RenderDoc не сходятся с событием Frame Debugger (индексы, вершины или "
                         "пиксели больше цели): их пиксели и вершины могут быть от другого draw — отмечены «!».",
    "device_discard_unshaded": "У {n} draw с discard (alpha clip) пиксели не пересчитаны без discard ({error}): они "
                               "занижены, считаны только выжившие фрагменты.",
    "pass_unpriced": "проход {pass}: нет цены ({error})",
    "variants_unchecked": "Варианты не сверены с шейдерами (редактор не отвечал или --no-compile): цены могут быть "
                          "устаревшими.",
    "no_snapshots": "В проекте нет снимков кадра: глобальные keywords пайплайна (свет, тени, Forward+) не добавлены, "
                    "а проходы взяты все, кроме служебных.",
}

class _Params(dict):
    def __missing__(self, key):
        return "?"


def text(w):
    """Plain strings come from older results and pass through."""
    if isinstance(w, str):
        return w
    body = WARNINGS.get(w.get("code"), w.get("code") or "").format_map(_Params(w))
    return f"{w['side']}: {body}" if w.get("side") else body


def export():
    return {"warnings": WARNINGS}
