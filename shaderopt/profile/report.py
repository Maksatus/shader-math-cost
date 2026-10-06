"""Report v1 (plan item A1.6, decision D-09): report.csv + report.html from measurements.jsonl.

One row per shader variant file (pass x keywords x stage x API) with the heaviness
(A1.5) on every measured core, the worst over cores and the maximum per
architecture. Fragment and vertex shaders are ranked separately. The HTML is one
self-contained file (no network), sortable and filterable; it stays local.
"""
import csv
import html
import json
import os
from collections import OrderedDict

from shaderopt.profile import score as heavy

ARCHS = ("Bifrost", "Valhall", "Arm 5th Generation")
ARCH_SHORT = {"Bifrost": "Bifrost", "Valhall": "Valhall", "Arm 5th Generation": "5th Gen"}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def build_rows(records, fp16_threshold=heavy.FP16_THRESHOLD):
    """records (measurements.jsonl lines) -> (rows, cores, failures)."""
    rows, cores, failures = OrderedDict(), OrderedDict(), []
    for rec in records:
        if not rec.get("ok", True):
            failures.append(rec)
            continue
        cores[rec["core"]] = rec["arch"]
        key = (rec["file"], rec["api"])
        row = rows.setdefault(key, {
            "file": rec["file"], "shader": rec.get("shader") or os.path.splitext(os.path.basename(rec["file"]))[0],
            "pass": rec.get("pass", ""), "keywords": rec.get("keywords", []), "stage": rec["stage"],
            "api": rec["api"], "scores": {}})
        row["scores"][rec["core"]] = heavy.score(rec, fp16_threshold)
    for row in rows.values():
        sc = row["scores"]
        worst = max(sc, key=lambda c: sc[c]["cycles"])
        row["worst"] = {"core": worst, "cycles": sc[worst]["cycles"]}
        row["arch"] = {}
        for a in ARCHS:
            vals = [s["cycles"] for c, s in sc.items() if cores[c] == a]
            row["arch"][ARCH_SHORT[a]] = max(vals) if vals else None
        row["flags"] = [f for f in heavy.FLAGS if any(f in s["flags"] for s in row["scores"].values())]
        row["work_regs"] = max(s["work_regs"] for s in row["scores"].values())
        fp = [s["fp16_pct"] for s in row["scores"].values() if s["fp16_pct"] is not None]
        row["fp16_pct"] = min(fp) if fp else None
    ordered = sorted(rows.values(), key=lambda r: (r["stage"], -r["worst"]["cycles"]))
    for stage in ("fragment", "vertex", "compute"):
        for i, r in enumerate((r for r in ordered if r["stage"] == stage), 1):
            r["rank"] = i
    return ordered, list(cores.items()), failures


def write_csv(rows, cores, path):
    cols = (["stage", "rank", "shader", "pass", "keywords", "api", "file", "worst_cycles", "worst_core"]
            + [f"{ARCH_SHORT[a]}_cycles" for a in ARCHS]
            + [f"{c}_{k}" for c, _ in cores for k in ("cycles", "bound", "path")]
            + ["work_regs", "fp16_pct", "flags"])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            line = [r["stage"], r["rank"], r["shader"], r["pass"], " ".join(r["keywords"]), r["api"], r["file"],
                    r["worst"]["cycles"], r["worst"]["core"]] + [r["arch"][ARCH_SHORT[a]] for a in ARCHS]
            for c, _ in cores:
                s = r["scores"].get(c)
                line += [s["cycles"], "+".join(s["bound"]), s["path"]] if s else ["", "", ""]
            line += [r["work_regs"], r["fp16_pct"], " ".join(r["flags"])]
            w.writerow(["" if v is None else v for v in line])


def write_html(rows, cores, failures, path, title, meta, top=20):
    out_dir = os.path.dirname(os.path.abspath(path))
    data = {
        "title": title, "meta": meta, "top": top,
        "cores": [{"name": c, "arch": ARCH_SHORT.get(a, a)} for c, a in cores],
        "archs": [ARCH_SHORT[a] for a in ARCHS if any(x == a for _, x in cores)],
        "flags": list(heavy.FLAGS),
        "rows": [{**r, "href": os.path.relpath(os.path.join(meta["folder"], r["file"]), out_dir).replace("\\", "/")}
                 for r in rows],
        "failures": [{"file": f["file"], "core": f["core"], "error": f["error"]} for f in failures],
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", blob)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page)


def run(folder, out=None, top=20, fp16_threshold=heavy.FP16_THRESHOLD, title=None):
    """measurements.jsonl in `folder` -> report.csv + report.html in `out` (default: folder)."""
    out = out or folder
    records = load(os.path.join(folder, "measurements.jsonl"))
    rows, cores, failures = build_rows(records, fp16_threshold)
    malioc = sorted({r.get("malioc", "") for r in records if r.get("ok", True)})
    drivers = sorted({f"{r['core']} {r['driver']}" for r in records if r.get("ok", True)})
    meta = {"folder": os.path.abspath(folder), "malioc": ", ".join(malioc), "drivers": drivers,
            "fp16_threshold": fp16_threshold}
    os.makedirs(out, exist_ok=True)
    write_csv(rows, cores, os.path.join(out, "report.csv"))
    write_html(rows, cores, failures, os.path.join(out, "report.html"),
               title or f"shaderopt: {os.path.basename(os.path.abspath(folder))}", meta, top)
    return rows, cores, failures


TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1b1f24; --muted: #667080; --line: #e3e6ea;
  --accent: #2f6fde; --chip: #eef1f5; --hover: #f0f4fb;
  --cheap: #1f9d55; --medium: #c98a00; --heavy: #d0402b;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111418; --panel: #191d23; --text: #e6e9ee; --muted: #8b95a5; --line: #2a3039;
    --accent: #6a9cff; --chip: #232932; --hover: #20262f;
    --cheap: #3ccf7f; --medium: #e8b030; --heavy: #ff6b55;
  }
}
:root[data-theme="dark"] {
  --bg: #111418; --panel: #191d23; --text: #e6e9ee; --muted: #8b95a5; --line: #2a3039;
  --accent: #6a9cff; --chip: #232932; --hover: #20262f;
  --cheap: #3ccf7f; --medium: #e8b030; --heavy: #ff6b55;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header, main { max-width: 1800px; margin: 0 auto; padding: 0 16px; }
header { padding-top: 18px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 22px 0 6px; }
h2 small { color: var(--muted); font-weight: 400; font-size: 13px; }
.sub { color: var(--muted); margin: 0 0 4px; }
details.help { color: var(--muted); font-size: 13px; margin: 6px 0; }
details.help summary { cursor: pointer; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin: 12px 0 8px; }
.group { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.group > label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { border: 0; background: transparent; color: var(--text); padding: 5px 11px; cursor: pointer; font: inherit; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button.on { background: var(--accent); color: #fff; }
input[type=search], select { background: var(--panel); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: 5px 9px; font: inherit; }
input[type=search] { width: 220px; max-width: 100%; }
.chipbtn { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 999px;
  padding: 3px 10px; cursor: pointer; font: inherit; font-size: 12px; }
.chipbtn.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn { border: 1px solid var(--line); background: var(--panel); color: var(--text); border-radius: 8px;
  padding: 5px 10px; cursor: pointer; font: inherit; }
.wrap { overflow: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; max-height: 75vh; }
table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
thead th { position: sticky; top: 0; background: var(--panel); z-index: 2; font-weight: 600; font-size: 12px;
  vertical-align: bottom; cursor: pointer; user-select: none; }
thead th small { display: block; font-weight: 400; color: var(--muted); font-size: 10px; }
thead th.sorted { color: var(--accent); }
th.l, td.l { text-align: left; }
td.name { position: sticky; left: 0; background: var(--panel); z-index: 1; max-width: 420px; white-space: normal; }
thead th.name { left: 0; z-index: 3; }
tbody tr:hover td { background: var(--hover); }
.sh { font-weight: 600; }
.pass { color: var(--muted); }
.kw { display: inline-block; font-family: ui-monospace, Consolas, monospace; font-size: 11px; background: var(--chip);
  border-radius: 4px; padding: 0 4px; margin: 1px 2px 1px 0; color: var(--muted); }
.num { font-variant-numeric: tabular-nums; }
td.big { font-weight: 700; }
.bd { display: block; font-size: 10px; color: var(--muted); }
.flag { display: inline-block; font-size: 11px; font-weight: 600; padding: 1px 7px; border-radius: 999px; margin: 1px 2px; }
.flag.regs_gt32, .flag.spilling { color: var(--heavy); background: color-mix(in srgb, var(--heavy) 14%, transparent); }
.flag.sfu_bound, .flag.low_fp16 { color: var(--medium); background: color-mix(in srgb, var(--medium) 14%, transparent); }
.flag.dynamic_loop { color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.file { font-family: ui-monospace, Consolas, monospace; font-size: 11px; }
.empty { color: var(--muted); padding: 14px; }
.fail { color: var(--heavy); white-space: pre-wrap; font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
.gl { border-left: 2px solid var(--line); }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <p class="sub" id="meta"></p>
  <details class="help"><summary>Как читать</summary>
    <p><b>Цена</b> — циклы самого загруженного конвейера (узкое место) на longest path, по оценке malioc для этого ядра;
    под числом — какой это конвейер (fma, cvt, sfu — арифметика; ls — load/store; v — varying; t — текстуры).
    Для вертексных шейдеров — Position + Varying. <b>Худшее</b> — максимум по ядрам; колонки архитектур — максимум по ядрам архитектуры.
    Циклы ядер одного поколения сравнимы; между поколениями ширина конвейеров разная (у 5th Gen FMA вчетверо шире, чем у Valhall,
    а load/store — нет), сравнивать их стоит по рангу шейдера, а не по числу.</p>
    <p>Флаги: <span class="flag regs_gt32">regs_gt32</span> больше 32 рабочих регистров — половинная занятость потоков;
    <span class="flag spilling">spilling</span> регистры ушли в память;
    <span class="flag low_fp16">low_fp16</span> доля 16-битной арифметики ниже порога;
    <span class="flag sfu_bound">sfu_bound</span> упирается в SFU (трансцендентные функции, деления);
    <span class="flag dynamic_loop">dynamic_loop</span> longest path = N/A (цикл с длиной из uniform), взята цена total ≈ одна итерация цикла.</p>
    <p>Это статическая оценка malioc для одного драйвера, без состояния пайплайна и значений uniform.</p>
  </details>
</header>
<main>
  <div class="controls">
    <div class="group"><label>Поиск</label><input type="search" id="q" placeholder="шейдер, пасс, keyword, файл"></div>
    <div class="group"><label>API</label><div class="seg" id="api"></div></div>
    <div class="group"><label>Пасс</label><select id="pass"></select></div>
    <div class="group"><label>Флаги</label><span id="flags"></span></div>
    <div class="group"><button class="btn" id="all"></button></div>
  </div>
  <section id="fragment"></section>
  <section id="vertex"></section>
  <section id="failures"></section>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const st = { q: "", api: "all", pass: "", flags: new Set(), all: false,
             sort: { fragment: ["worst", -1], vertex: ["worst", -1] } };
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = v => v == null ? "—" : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);

document.getElementById("title").textContent = D.title;
document.getElementById("meta").textContent =
  `malioc ${D.meta.malioc} · ядра: ${D.cores.map(c => c.name).join(", ")} · ${D.rows.length} вариантов · ${D.meta.folder}`;
document.title = D.title;

const apis = ["all", ...new Set(D.rows.map(r => r.api))];
const apiSeg = document.getElementById("api");
apis.forEach(a => { const b = document.createElement("button"); b.textContent = a === "all" ? "все" : a;
  b.onclick = () => { st.api = a; render(); }; b.dataset.v = a; apiSeg.appendChild(b); });
const passSel = document.getElementById("pass");
passSel.innerHTML = '<option value="">все</option>' + [...new Set(D.rows.map(r => r.pass).filter(Boolean))]
  .map(p => `<option>${esc(p)}</option>`).join("");
passSel.onchange = () => { st.pass = passSel.value; render(); };
const flagBox = document.getElementById("flags");
D.flags.forEach(f => { const b = document.createElement("button"); b.className = "chipbtn"; b.textContent = f;
  b.onclick = () => { st.flags.has(f) ? st.flags.delete(f) : st.flags.add(f); render(); }; b.dataset.v = f; flagBox.appendChild(b); });
document.getElementById("q").oninput = e => { st.q = e.target.value.toLowerCase(); render(); };
document.getElementById("all").onclick = () => { st.all = !st.all; render(); };

function key(r, k) {
  if (k === "worst") return r.worst.cycles ?? -1;
  if (k.startsWith("arch:")) return r.arch[k.slice(5)] ?? -1;
  if (k.startsWith("core:")) { const s = r.scores[k.slice(5)]; return s ? s.cycles : -1; }
  if (k === "name") return (r.shader + " " + r.pass + " " + r.keywords.join(" ")).toLowerCase();
  if (k === "regs") return r.work_regs ?? -1;
  if (k === "fp16") return r.fp16_pct ?? 999;
  return r[k] ?? "";
}
function visible(r) {
  if (st.api !== "all" && r.api !== st.api) return false;
  if (st.pass && r.pass !== st.pass) return false;
  for (const f of st.flags) if (!r.flags.includes(f)) return false;
  if (st.q) { const t = [r.shader, r.pass, r.keywords.join(" "), r.file, r.api].join(" ").toLowerCase();
    if (!t.includes(st.q)) return false; }
  return true;
}
function heat(v, max) {
  if (v == null || !max) return "";
  const t = Math.min(1, v / max);
  return `background: color-mix(in srgb, var(--heavy) ${Math.round(t * 28)}%, transparent)`;
}
function table(stage) {
  const [sk, sd] = st.sort[stage];
  let rows = D.rows.filter(r => r.stage === stage && visible(r));
  const total = rows.length;
  rows.sort((a, b) => { const x = key(a, sk), y = key(b, sk); return (x < y ? -1 : x > y ? 1 : 0) * sd; });
  if (!st.all) rows = rows.slice(0, D.top);
  const max = Math.max(0, ...D.rows.filter(r => r.stage === stage).map(r => r.worst.cycles || 0));
  const th = (k, label, cls = "", small = "") =>
    `<th class="${cls} ${sk === k ? "sorted" : ""}" data-k="${k}" data-stage="${stage}">${label}${sk === k ? (sd < 0 ? " ↓" : " ↑") : ""}${small ? `<small>${small}</small>` : ""}</th>`;
  let h = `<h2>${stage === "fragment" ? "Пиксельные" : "Вертексные"} шейдеры <small>${st.all ? total : Math.min(total, D.top)} из ${total}${stage === "vertex" ? " · Position + Varying" : ""}</small></h2>`;
  if (!total) return h + '<div class="wrap"><div class="empty">Нет шейдеров под фильтр.</div></div>';
  h += `<div class="wrap"><table><thead><tr>${th("rank", "#")}${th("name", "Шейдер · пасс · keywords", "l name")}${th("api", "API", "l")}`
     + th("worst", "Худшее", "gl", "циклы")
     + D.archs.map(a => th("arch:" + a, a, "", "циклы")).join("")
     + D.cores.map((c, i) => th("core:" + c.name, c.name.replace("Mali-", "").replace("Immortalis-", ""), i ? "" : "gl", c.arch)).join("")
     + `${th("regs", "Регистры", "gl")}${th("fp16", "fp16 %")}<th class="l">Флаги</th><th class="l">Файл</th></tr></thead><tbody>`;
  for (const r of rows) {
    h += `<tr><td class="num">${r.rank}</td><td class="l name"><span class="sh">${esc(r.shader)}</span> <span class="pass">· ${esc(r.pass)}</span><br>`
       + (r.keywords.length ? r.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">без keywords</span>') + `</td>`
       + `<td class="l">${esc(r.api)}</td>`
       + `<td class="num big gl" style="${heat(r.worst.cycles, max)}">${fmt(r.worst.cycles)}<span class="bd">${esc((r.worst.core || "").replace("Mali-", ""))}</span></td>`
       + D.archs.map(a => `<td class="num">${fmt(r.arch[a])}</td>`).join("")
       + D.cores.map((c, i) => { const s = r.scores[c.name];
           if (!s) return `<td class="num ${i ? "" : "gl"}">—</td>`;
           return `<td class="num ${i ? "" : "gl"}" title="${s.cycles} cycles, ${s.path}">${fmt(s.cycles)}<span class="bd">${esc(s.bound.join("+"))}${s.path === "total" ? " · total" : ""}</span></td>`; }).join("")
       + `<td class="num gl">${r.work_regs ?? "—"}</td><td class="num">${r.fp16_pct ?? "—"}</td>`
       + `<td class="l">${r.flags.map(f => `<span class="flag ${f}">${f}</span>`).join("")}</td>`
       + `<td class="l"><a class="file" href="${esc(encodeURI(r.href))}">${esc(r.file)}</a></td></tr>`;
  }
  return h + "</tbody></table></div>";
}
function render() {
  apiSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.api));
  flagBox.querySelectorAll("button").forEach(b => b.classList.toggle("on", st.flags.has(b.dataset.v)));
  document.getElementById("all").textContent = st.all ? `Только топ-${D.top}` : "Показать все";
  for (const s of ["fragment", "vertex"]) document.getElementById(s).innerHTML = table(s);
  document.querySelectorAll("thead th[data-k]").forEach(th => th.onclick = () => {
    const s = th.dataset.stage, k = th.dataset.k, cur = st.sort[s];
    st.sort[s] = [k, cur[0] === k ? -cur[1] : (k === "name" || k === "api" || k === "rank" ? 1 : -1)]; render(); });
  document.getElementById("failures").innerHTML = D.failures.length
    ? `<h2>Не скомпилировались <small>${D.failures.length}</small></h2><div class="wrap"><table><tbody>`
      + D.failures.map(f => `<tr><td class="l">${esc(f.file)}</td><td class="l">${esc(f.core)}</td><td class="l fail">${esc(f.error)}</td></tr>`).join("")
      + "</tbody></table></div>" : "";
}
render();
</script>
</body>
</html>
"""
