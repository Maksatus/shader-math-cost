# Shader Math Cost (Mali)

Сколько стоят функции в шейдерах на GPU Mali: 120 функций, 32 GPU, OpenGL ES и Vulkan.
Сайт: https://maksatus.github.io/shader-math-cost/. Цена в FMA: «sin = 8» — как 8 обычных операций.

## ParetoGPU — профилировщик кадра и шейдеров

Что нужно: Python 3.10+, [Arm Performance Studio](https://developer.arm.com/Tools%20and%20Software/Arm%20Performance%20Studio) (malioc), RenderDoc, Unity с открытым проектом.

Запуск: `start.bat` (проверит, что всё установлено, и откроет окно) или `python -m paretogpu ui`.

Вкладки окна:
- **Проверка кадра** — Play Mode в Unity, кнопка; снимок кадра, цена на Mali, самые дорогие шейдеры.
- **Отчёты** — посчитанные кадры, пересчёт после правки шейдера, «Почему тяжёлые» (разбор по строкам).
- **Сравнение** — два кадра или два материала: что подорожало и почему (шейдеры, объём работы, варианты).
- **Материал** — один материал: варианты, цена пикселя и вершины, цена каждой строки.
- **Цена функций**, **Справочник** — таблица с сайта и как читать отчёты.

Отчёты и снимки лежат в `paretogpu/out` и в git не попадают. Консольные команды: `python -m paretogpu -h`.

## Данные сайта

- `python -m paretogpu bench` — пересчитать таблицу цен (`docs/`); функции в `paretogpu/model/functions.py`.
- Аппроксимации (кнопка «≈ быстрее»): `bench/approx/build_approx.py` → `docs/approx.js`; нужны numpy, scipy.

## Код

```
paretogpu/
  model/     типы и константы, шаги прогресса (phases.py)
  core/      чистые расчёты: цена, кадр, сравнение, циклы, абляция, материалы
  adapters/  malioc, Unity (мост к редактору, C# в cs/), RenderDoc
  store/     paretogpu/out: снимки, варианты, расчёты, описания результатов, memo
  views/     страницы результатов, CSV, Reporter, messages.py (все тексты)
  app/       сценарии команд и движок «что ещё не сделано» (компиляция → замер → циклы)
  features/  команды: аргументы, фазы, результат — из этого строятся CLI и окно
  cli.py, ui/
```

- Импорты только вниз: `cli, ui → features → app → adapters | store | views → core → model` (`tests/test_layers.py`).
- Новая команда: `app/<имя>.py` (сценарий) + `features/<имя>.py` (`Command`) + `views/static/result/<имя>.js` (страница). CLI, форма, список результатов и названия фаз появляются сами.
- Предупреждения и подписи — коды из `core`/`app`, слова в `views/messages.py`.
- Дорогое запоминается по содержимому: malioc в `bench/.cache`, правила в `paretogpu/out/_cache`; у правила есть версия.
- Тесты: `python -m unittest discover tests`. Регрессия на своих данных: `python tools/regression.py baseline` до, `check` после.

Подробности и план: `plans/shader-optimizer-plan.md` (раздел 3 — что есть, раздел 7 — что дальше).
