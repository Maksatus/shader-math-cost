"""HTML pages of the results: self-contained files (data embedded as JSON), they stay local.

  templates/result_view.html   one page for every result, by its "kind": the frame report (kind "cost", plan item K1.6,
                               decision D-09) of frame_cost.json — the events sorted by their total with the summary by
                               stage above, the main core (D-17) first and the other cores a switch, grouping by shader,
                               variant, object or render target; the comparison of two cost runs, two materials, one
                               material, the hotspots of a frame. Its parts are in static/: shared/helpers.js and
                               shared/tips.js (the window loads the same files as /shared/shared/...), result/core.js
                               (the page's helpers, render() by kind), one result/<kind>.js per view (it sets
                               VIEWS[kind]), result/view.css or the view's own result/<kind>.css, result/main.js;
                               a new kind of result is one new <kind>.js.
  static/theme.css             the palette and fonts every page shares.
"""
import html
import json
import os

from paretogpu.views import messages

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "templates")
STATIC = os.path.join(HERE, "static")
RESULT = os.path.join(STATIC, "result")


def _template(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


def _static(*path):
    with open(os.path.join(STATIC, *path), encoding="utf-8") as f:
        return f.read()


def result_scripts():
    """The scripts of the result page in order: the shared helpers, core.js, every view, the shared tips, main.js."""
    views = sorted(fn for fn in os.listdir(RESULT) if fn.endswith(".js") and fn not in ("core.js", "main.js"))
    return ([("shared", "helpers.js"), ("result", "core.js")] + [("result", fn) for fn in views]
            + [("shared", "tips.js"), ("result", "main.js")])


def page_css(kind):
    """The view's own <kind>.css if it has one, else view.css."""
    own = f"{kind}.css"
    return own if kind and os.path.isfile(os.path.join(RESULT, own)) else "view.css"


def render_result(doc, title="Сравнение кадров"):
    # < > & as JSON escapes: no name can close the <script> or open a comment in it
    data = json.dumps(doc, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    page = (_template("result_view.html").replace("/*THEME*/\n", _static("theme.css"))
            .replace("/*PAGE_CSS*/\n", _static("result", page_css(doc.get("kind"))))
            .replace("/*SCRIPTS*/\n", "".join(_static(*fn) for fn in result_scripts())))
    msg = json.dumps(messages.export(), ensure_ascii=False).replace("<", "\\u003c")
    return (page.replace("/*COMPARE_DATA*/null", data).replace("/*MESSAGES*/null", msg)
            .replace("<title>Сравнение</title>", f"<title>{html.escape(title)}</title>"))


def write_frame_report(cost, path, title):
    """The frame report: frame_cost.json rendered as the result of kind "cost" (the file itself has no kind)."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_result({"kind": "cost", **cost}, title))


def write_result(doc, out_dir, name, title="Сравнение кадров"):
    """<out_dir>/<name>.json and <name>.html; returns the path of the page."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, name + ".html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_result(doc, title))
    return os.path.join(out_dir, name + ".html")
