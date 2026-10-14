# Shader Math Cost (Mali)

Цена функций в шейдерах на GPU Mali (120 функций, 32 GPU, GLES и Vulkan), в FMA: «sin = 8» — как 8 обычных операций.
Сайт: https://maksatus.github.io/shader-math-cost/.

## ParetoGPU — цена кадра на Mali

Нужно: Python 3.10+, [Arm Performance Studio](https://developer.arm.com/Tools%20and%20Software/Arm%20Performance%20Studio) (malioc), RenderDoc, Unity с открытым проектом.
Запуск: `start.bat` или `python -m paretogpu ui`.

- **Проверка кадра** — Unity в Play Mode → «Проверить кадр»: снимок, цена на Mali, самые дорогие шейдеры.
  С телефона (USB, development build, Vulkan): выберите телефон → «Проверить кадр» запустит игру → «Снять кадр».
- **Отчёты**, **Сравнение** (кадры, материалы), **Материал**, **Цена функций**, **Справочник**.

Снимки и отчёты — в `paretogpu/out` (не в git). Консоль: `python -m paretogpu -h`.

## Данные сайта

- `python -m paretogpu bench` — таблица цен (`docs/`), функции в `paretogpu/model/functions.py`.
- Аппроксимации: `bench/approx/build_approx.py` → `docs/approx.js` (numpy, scipy); проверка полным перебором — `bench/approx/check.py`.

План: `plans/shader-optimizer-plan.md`.
