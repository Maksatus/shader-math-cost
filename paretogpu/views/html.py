"""HTML pages of the results: self-contained files (data embedded as JSON), they stay local.

  templates/frame_report.html  frame report (plan item K1.6, decision D-09) of frame_cost.json: one main table
                               sorted by the event total — object · shader · pass · keywords · pixels · vertices ·
                               pixel price · vertex price · total · % of frame — with the summary by stage above it.
                               The main core (D-17) is shown first, the other cores are a switch; the table can be
                               grouped by shader, variant, object or render target. Registers, spilling and fp16 are
                               hints under the name, not columns.
  templates/result_view.html   one page for the other results, by their "kind": the comparison of two cost runs,
                               two materials, one material, the hotspots of a frame.
"""
import html
import json
import os

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _template(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


def write_frame_report(cost, path, title):
    # < > & as JSON escapes: no name can close the <script> or open a comment in it
    blob = json.dumps(cost, ensure_ascii=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    page = _template("frame_report.html").replace("__TITLE__", html.escape(title)).replace("__DATA__", blob)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)


def render_result(doc, title="Сравнение кадров"):
    data = json.dumps(doc, ensure_ascii=False).replace("</", "<\\/")
    return (_template("result_view.html").replace("/*COMPARE_DATA*/null", data)
            .replace("<title>Сравнение</title>", f"<title>{title}</title>"))


def write_result(doc, out_dir, name, title="Сравнение кадров"):
    """<out_dir>/<name>.json and <name>.html; returns the path of the page."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, name + ".html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render_result(doc, title))
    return os.path.join(out_dir, name + ".html")
