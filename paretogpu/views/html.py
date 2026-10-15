"""Self-contained result pages. A new result kind = one static/result/<kind>.js setting VIEWS[kind]
(+ optional <kind>.css); shared/*.js are also served to the UI window as /shared/shared/..."""
import base64
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
    views = sorted(fn for fn in os.listdir(RESULT) if fn.endswith(".js") and fn not in ("core.js", "main.js"))
    return ([("shared", "helpers.js"), ("result", "core.js")] + [("result", fn) for fn in views]
            + [("shared", "tips.js"), ("result", "main.js")])


def page_css(kind):
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


def write_frame_report(cost, path, title, thumb_path=None):
    """frame_cost.json has no "kind" field; the page needs "cost". thumb_path: the snapshot's frame preview, inlined."""
    thumb = None
    try:
        with open(thumb_path or "", "rb") as f:
            thumb = "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_result({"kind": "cost", **cost, "thumb": thumb}, title))


def write_result(doc, out_dir, name, title="Сравнение кадров"):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, name + ".html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_result(doc, title))
    return os.path.join(out_dir, name + ".html")
