"""Run by start.bat, which branches on the exit code: 0 all fine, 1 warnings only, 2 a failure. Installs nothing."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OK, WARN, FAIL = "ok", "warn", "fail"
TAGS = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}

results = []  # (status, what, detail)


def report(status, what, detail=""):
    results.append((status, what, detail))
    print(f"  {TAGS[status]} {what}" + (f": {detail}" if detail else ""))


def check_python():
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        report(FAIL, "Python", f"{ver}, нужен 3.10+ ({sys.executable})")
    else:
        report(OK, "Python", f"{ver} ({sys.executable})")


def check_numpy():
    try:
        import numpy
        report(OK, "numpy", numpy.__version__)
    except ImportError:
        report(WARN, "numpy", f"не установлен: цена кадра не посчитает пиксели draw (квады). {sys.executable} -m pip install numpy")


def check_files():
    need = ["docs/index.html", "docs/data.js", "docs/approx.js", "paretogpu/ui/static/index.html", "paretogpu/ui/server.py"]
    missing = [p for p in need if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        report(FAIL, "файлы сайта", "нет " + ", ".join(missing))
    else:
        report(OK, "файлы сайта", os.path.join(ROOT, "docs"))


def check_malioc():
    from paretogpu.adapters import malioc as mali
    try:
        ver = mali.version()
    except mali.MaliocError:
        ver = ""
    if ver:
        report(OK, "malioc (Arm Performance Studio)", f"v{ver} ({mali.MALIOC})")
    else:
        report(WARN, "malioc (Arm Performance Studio)",
               "не найден: цена кадра, материалов и таблица сайта не будут работать. Скачать: "
               "https://developer.arm.com/Tools%20and%20Software/Arm%20Performance%20Studio или задать MALIOC")


def check_renderdoc():
    from paretogpu.adapters import renderdoc
    try:
        report(OK, "RenderDoc", renderdoc.qrenderdoc())
    except renderdoc.RenderDocError:
        report(WARN, "RenderDoc", "не найден: снимок кадра в редакторе не будет работать. Скачать: "
                                  "https://renderdoc.org/builds или задать RENDERDOC_DIR")
    try:
        renderdoc.spirv_as()
    except renderdoc.RenderDocError:
        report(WARN, "spirv-as (RenderDoc)", "нет в plugins/spirv RenderDoc: Vulkan-варианты не соберутся")


def check_device():
    from paretogpu.adapters import renderdoc
    try:
        report(OK, "adb", renderdoc.adb())
    except renderdoc.RenderDocError:
        report(WARN, "adb", "не найден: кадр с телефона не будет работать. Android SDK platform-tools или задать ADB")
    try:
        report(OK, "RenderDoc for Arm", renderdoc.arm_qrenderdoc())
    except renderdoc.RenderDocError:
        report(WARN, "RenderDoc for Arm", "не найден: кадр с телефона не будет работать. Ставится с Arm Performance "
                                          "Studio или задать RENDERDOC_ARM_DIR")


def check_unity():
    from paretogpu.adapters.unity import cli as export
    cli = export.unity_cli()
    if cli:
        report(OK, "Unity CLI (unity)", cli)
    else:
        report(WARN, "Unity CLI (unity)", "нет в PATH: снимок кадра и материалы с открытым редактором не будут работать")


def main():
    print("Проверка окружения ParetoGPU\n")
    for check in (check_python, check_numpy, check_files, check_malioc, check_renderdoc, check_device, check_unity):
        try:
            check()
        except Exception as e:  # one broken check must not hide the others
            report(WARN, check.__name__[len("check_"):], f"проверка упала: {e!r}")
    worst = FAIL if any(s == FAIL for s, _, _ in results) else WARN if any(s == WARN for s, _, _ in results) else OK
    print("\n" + {OK: "Всё на месте.", WARN: "Интерфейс запустится, но часть команд работать не будет (см. WARN).",
                  FAIL: "Интерфейс не запустится (см. FAIL)."}[worst])
    return {OK: 0, WARN: 1, FAIL: 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
