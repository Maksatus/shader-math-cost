"""What the local UI and its commands need, checked: `python -m paretogpu.doctor` (start.bat runs it).

Every check prints one line: [ OK ], [WARN] (some commands will not work) or [FAIL] (the UI will not start).
Nothing is installed: a missing tool is only reported, with where to get it.
Exit code: 0 all fine, 1 warnings only, 2 a failure.
"""
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


def check_files():
    need = ["docs/index.html", "docs/data.js", "docs/approx.js", "paretogpu/ui/app.html", "paretogpu/ui/server.py"]
    missing = [p for p in need if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        report(FAIL, "файлы сайта", "нет " + ", ".join(missing))
    else:
        report(OK, "файлы сайта", os.path.join(ROOT, "docs"))


def check_malioc():
    from paretogpu import mali
    try:
        ver = mali.version()
    except mali.MaliocError:
        ver = ""
    if ver:
        report(OK, "malioc (Arm Performance Studio)", f"v{ver} ({mali.MALIOC})")
    else:
        report(WARN, "malioc (Arm Performance Studio)",
               "не найден: замеры (cost, measure, export --measure, сайт) не будут работать. Скачать: "
               "https://developer.arm.com/Tools%20and%20Software/Arm%20Performance%20Studio или задать MALIOC")


def check_renderdoc():
    from paretogpu.frame import renderdoc
    try:
        report(OK, "RenderDoc", renderdoc.qrenderdoc())
    except renderdoc.RenderDocError:
        report(WARN, "RenderDoc", "не найден: снимок кадра не будет работать. Скачать: "
                                  "https://renderdoc.org/builds или задать RENDERDOC_DIR")


def check_unity():
    from paretogpu.unity import export
    cli = export.unity_cli()
    if cli:
        report(OK, "Unity CLI (unity)", cli)
    else:
        report(WARN, "Unity CLI (unity)", "нет в PATH: frame и export с открытым редактором не будут работать")
    if os.environ.get("UNITY_EDITOR"):
        ok = os.path.exists(os.environ["UNITY_EDITOR"])
        report(OK if ok else WARN, "Unity Editor (UNITY_EDITOR)",
               os.environ["UNITY_EDITOR"] + ("" if ok else " — файла нет"))
        return
    found = []
    for d in export.hub_editor_dirs():
        if os.path.isdir(d):
            found += [f"{v} ({d})" for v in sorted(os.listdir(d))
                      if os.path.exists(os.path.join(d, v, "Editor", "Unity.exe"))]
    if found:
        report(OK, "Unity Editor (Hub)", ", ".join(found))
    else:
        report(WARN, "Unity Editor (Hub)",
               "редакторы не найдены: export в batchmode не будет работать (или задать UNITY_EDITOR)")


def main():
    print("Проверка окружения ParetoGPU\n")
    for check in (check_python, check_files, check_malioc, check_renderdoc, check_unity):
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
