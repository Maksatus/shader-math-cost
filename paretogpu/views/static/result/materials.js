let selN = D && D.default_n;
const kws = k => k.length ? k.map(x => `<span class="kwc">${esc(x)}</span>`).join(" ") : `<span class="muted">без keywords</span>`;
const dp = (a, b) => a == null || b == null ? "" : `<span class="${cls(b - a)}">${pct(b - a, a)}</span>`;
const c1 = x => x == null ? "—" : (+x).toFixed(x >= 100 ? 0 : 1);

function matSide(m, tag) {
  return `<div class="side ${tag === "B" ? "b" : ""}"><span class="tag">${tag}</span><b>${esc(m.material)}</b>
    <div class="small muted" style="font-family:var(--mono);word-break:break-all">${esc(m.path)}</div>
    <div class="small" style="margin-top:4px">${esc(m.shader)}</div>
    <div class="small" style="margin-top:4px">${kws(m.keywords)}</div></div>`;
}

let selPart = "px", selApi = D && D.api;
const PR = p => (p && ((p.prices_by_api || {})[selApi] || (selApi === D.api ? p.prices : null))) || {};
const API_NAME = {vulkan: "Vulkan", gles: "GLES"};
const PIPES = [["arith", "A — арифметика"], ["fma", "FMA", 1], ["cvt", "CVT", 1], ["sfu", "SFU", 1],
  ["ls", "LS — память"], ["v", "V — varyings"], ["t", "T — текстуры"]];

const LABEL_W = 210, NUM_W = 96;
const cols = n => `<colgroup><col style="width:${LABEL_W}px">${`<col style="width:${NUM_W}px">`.repeat(n)}</colgroup>`;
// B − A of a price in % of A, coloured: cheaper green, dearer red
const dPct = (u, w) => u == null || w == null ? `<td class="n"></td>` : u === w ? `<td class="n muted">=</td>` : `<td class="n ${cls(w - u)}">${pct(w - u, u)}</td>`;

function pipesTable(xa, xb, k, n) {
  const ga = xa && xa[k + "_pipes"], gb = xb && xb[k + "_pipes"];
  if (!ga && !gb) return "";
  const la = ga && ga.longest[n], lb = gb && gb.longest[n];
  const top = c => { if (!c) return null; const v = ["arith", "ls", "v", "t"].filter(x => c[x] != null).map(x => c[x]); return v.length ? Math.max(...v) : null; };
  const ta = top(la), tb = top(lb);
  const has = key => [la, lb, ga && ga.shortest, gb && gb.shortest, ga && ga.total, gb && gb.total].some(c => c && c[key] != null);
  const v = (c, key, t) => { const x = c && c[key]; if (x == null) return `<td class="n">—</td>`;
    const hot = t != null && Math.abs(x - t) < 1e-6 && ["arith", "ls", "v", "t"].includes(key);
    return `<td class="n">${hot ? `<b class="hot" title="Самый загруженный блок: его циклы и есть цена (упор)">${c1(x)} ●</b>` : c1(x)}</td>`; };
  const d = (u, w) => u == null || w == null ? `<td class="n"></td>` : `<td class="n ${cls(w - u)}">${w === u ? "=" : (w > u ? "+" : "−") + c1(Math.abs(w - u))}</td>`;
  const dep = byNs(xa, k) || byNs(xb, k);
  const notes = [];
  if ((ga && ga.longest_na) || (gb && gb.longest_na)) notes.push(dep
    ? "У шейдера динамические циклы: malioc не считает самый длинный путь, он посчитан с циклами, прогнанными n раз."
    : "malioc не нашёл самый длинный путь: вместо него — «всего», поднятое до короткого пути там, где тот больше.");
  const rows = PIPES.filter(([key]) => has(key)).map(([key, label, sub]) => `<tr${sub ? ` class="subp"` : ""}><td title="${esc(PIPE[key][2])}" style="cursor:help">${sub ? "&nbsp;&nbsp;&nbsp;" : ""}${label}</td>
    ${v(la, key, ta)}${v(lb, key, tb)}${d(la && la[key], lb && lb[key])}
    ${v(ga && ga.shortest, key).replace('<td class="n">', '<td class="n gl">')}${v(gb && gb.shortest, key)}${v(ga && ga.total, key).replace('<td class="n">', '<td class="n gl">')}${v(gb && gb.total, key)}</tr>`).join("");
  return `<h3>По конвейерам <span class="muted small">циклов на ${PARTS[k][1]}, ${esc(core)}, ${API_NAME[selApi] || esc(selApi)}</span></h3>
    <table class="cmp">${cols(7)}<tr><th rowspan="2">Конвейер</th><th class="n grp" colspan="3" title="Самый дорогой путь через шейдер: каждый if идёт по более дорогой ветке. По нему считается цена; ● — самый загруженный блок, его циклы и есть цена">Длинный путь${dep ? ` (n = ${n})` : ""} — цена</th>
      <th class="n grp gl" colspan="2" title="Все if идут по дешёвой ветке">Короткий путь</th><th class="n grp gl" colspan="2" title="Каждая инструкция один раз: обе ветки if, тело цикла один раз. Объём кода, а не время">Всего (весь код)</th></tr>
      <tr><th class="n">A</th><th class="n">B</th><th class="n">B − A</th><th class="n gl">A</th><th class="n">B</th><th class="n gl">A</th><th class="n">B</th></tr>${rows}</table>
    <div class="small" style="margin-top:6px">${notes.length ? `<span class="muted">${notes.join(" ")}</span> ` : ""}${refLink("pipes", "блоки ядра и пути")}</div>`;
}
const PARTS = {px: ["Пиксель", "пиксель"], vtx: ["Вершина", "вершину"]};
// how much cheaper the cheaper one is, of the dearer one's price
const cheaper = (a, b) => a == null || b == null || a === b ? null : {who: b < a ? "B" : "A", by: Math.abs(a - b) / Math.max(a, b)};
// the price of this part changes with n (dynamic loops in this stage)
const byNs = (x, k) => !!x && new Set(D.ns.map(m => x[k][String(m)])).size > 1;
const p0 = x => (100 * x).toFixed(x < 0.1 ? 1 : 0) + "%";

function verdict(pa, pb, k) {
  const xa = pa && PR(pa)[core], xb = pb && PR(pb)[core];
  const n = String(selN), A = esc(D.a.material), B = esc(D.b.material);
  if (!xa || !xb) return `<div class="verdict"><div class="vh">Сравнить нечего: ${xa ? "у B" : "у A"} нет этого прохода на ${esc(core)}.</div></div>`;
  const c = cheaper(xa[k][n], xb[k][n]);
  const head = !c ? `<b>${A}</b> и <b>${B}</b> стоят одинаково`
    : `<span class="good">${c.who === "B" ? B : A}</span> <span class="muted">(${c.who})</span> дешевле <span class="bad">${c.who === "B" ? A : B}</span> <span class="muted">(${c.who === "B" ? "A" : "B"})</span> на <b>${p0(c.by)}</b>`;
  const notes = [];
  const dep = byNs(xa, k) || byNs(xb, k);
  if (dep) {
    const by = D.ns.map(m => [m, cheaper(xa[k][m], xb[k][m])]);
    const wins = w => by.filter(([, x]) => x && x.who === w).map(([m]) => m);
    const wa = wins("A"), wb = wins("B");
    const rng = w => { const v = by.filter(([, x]) => x && x.who === w).map(([, x]) => x.by); return v.length ? `${p0(Math.min(...v))}–${p0(Math.max(...v))}` : ""; };
    if (!wa.length && !wb.length) notes.push("одинаково при любом n");
    else if (!wa.length || !wb.length) notes.push(`при любом n циклов (${wa.length ? "A" : "B"} дешевле на ${rng(wa.length ? "A" : "B")})`);
    else notes.push(`<span class="bad">зависит от n:</span> A дешевле при n = ${wa.join(", ")}, B — при n = ${wb.join(", ")}`);
  } else notes.push("от n не зависит");
  if (D.cores.length > 1) {
    const wins = {A: [], B: [], "=": []};
    for (const cc of D.cores) {
      const u = PR(pa), w = PR(pb);
      const x = u && w && u[cc] && w[cc] ? cheaper(u[cc][k][n], w[cc][k][n]) : undefined;
      if (x !== undefined) wins[x ? x.who : "="].push(cc);
    }
    const tot = wins.A.length + wins.B.length + wins["="].length;
    const w = c ? c.who : null;
    notes.push(!w && wins["="].length === tot ? `поровну на всех ${tot} ядрах` : w && wins[w].length === tot ? `на всех ${tot} ядрах`
      : `ядра: ${["A", "B"].filter(x => wins[x].length).map(x => `${x} дешевле на ${wins[x].map(esc).join(", ")}`).join("; ")}${wins["="].length ? `; поровну на ${wins["="].map(esc).join(", ")}` : ""}`);
  }
  return `<div class="verdict ${c ? (c.who === "B" ? "vb" : "va") : ""}"><div class="vh">${head}</div>
    <div class="small muted">${PARTS[k][0].toLowerCase()} на ${esc(core)}${(D.apis || []).length > 1 ? ", " + (API_NAME[selApi] || esc(selApi)) : ""}${dep ? `, n = ${n}` : ""}: ${c1(xa[k][n])} против ${c1(xb[k][n])} циклов · ${notes.join(" · ")}</div></div>`;
}

function renderMaterials() {
  const pairs = D.pairs.map(([i, j]) => [i == null ? null : D.a.passes[i], j == null ? null : D.b.passes[j]]);
  const looped = pairs.some(pp => pp.some(p => p && Object.values(PR(p)).some(x => x.looped)));
  const A = esc(D.a.material), B = esc(D.b.material);
  const head = `<div class="card"><h1>Сравнение материалов</h1>
    <div class="ab">${matSide(D.a, "A")}<div class="arrow">→</div>${matSide(D.b, "B")}</div>
    <div class="mode small muted">Расчёт ${when(D.computed_at)} · ${esc(D.api)} · malioc ${esc(D.malioc || "?")} · ${esc(D.project)}</div>
    ${D.warnings.length ? `<div class="warn"><ul>${D.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
    <p class="small" style="margin:10px 0 0">${refLink("compare-materials", "Как читать сравнение")} · ${costHelp}</p></div>`;
  if (!D.cores.length) { $("#app").innerHTML = head; return; }
  const cores = D.cores.length > 1 ? `<div class="seg" id="cores">${D.cores.map(c => `<button data-c="${esc(c)}" class="${c === core ? "on" : ""}">${esc(c)}${c === D.main_core ? " ★" : ""}</button>`).join("")}</div>` : `<b>${esc(core)}</b>`;
  const parts = `<div class="seg" id="parts">${Object.entries(PARTS).map(([k, [t]]) => `<button data-k="${k}" class="${k === selPart ? "on" : ""}">${t}</button>`).join("")}</div>`;
  const apis = (D.apis || [D.api]).length > 1 ? `<div class="seg" id="apis">${D.apis.map(x => `<button data-a="${esc(x)}" class="${x === selApi ? "on" : ""}">${API_NAME[x] || esc(x)}</button>`).join("")}</div>` : "";
  const nsel = looped ? `<span class="small muted">n циклов</span><div class="seg" id="ns">${D.ns.map(n => `<button data-n="${n}" class="${n === selN ? "on" : ""}">${n}</button>`).join("")}</div>` : "";
  const tools = `<div class="card" style="padding:10px 16px"><div class="tools" style="margin:0">${parts}${apis}<span class="small muted" style="margin-left:8px">Ядро</span>${cores}<span style="flex:1"></span>${nsel}</div></div>`;
  const n = String(selN), k = selPart;
  const cell = (v, other) => `<td class="n ${v != null && other != null && v < other ? "good" : ""}">${v != null && other != null && v < other ? `<b>${c1(v)}</b>` : c1(v)}</td>`;
  const th = `<th class="n" title="${A}">A</th><th class="n" title="${B}">B</th><th class="n">B − A</th>`;
  const cards = pairs.map(([pa, pb]) => {
    const xa = pa && PR(pa)[core], xb = pb && PR(pb)[core];
    const name = pa && pb && pa.pass !== pb.pass ? `${esc(pa.pass)} ↔ ${esc(pb.pass)}` : esc((pa || pb).pass);
    const byN = byNs(xa, k) || byNs(xb, k) ? `<h3>${PARTS[k][0]} при разном n <span class="muted small">циклов на ${PARTS[k][1]}, ${esc(core)}, ${API_NAME[selApi] || esc(selApi)}</span></h3>
      <table class="cmp">${cols(3)}<tr><th>n циклов</th>${th}</tr>
      ${D.ns.map(m => { const u = xa && xa[k][String(m)], w = xb && xb[k][String(m)];
        return `<tr${m === selN ? ` class="sel"` : ""}><td><b>${m}</b></td>${cell(u, w)}${cell(w, u)}${dPct(u, w)}</tr>`; }).join("")}</table>` : "";
    const allCores = D.cores.length > 1 ? `<h3>${PARTS[k][0]} на всех ядрах <span class="muted small">${byNs(xa, k) || byNs(xb, k) ? `n = ${n}, ` : ""}циклов на ${PARTS[k][1]}</span></h3>
      <table class="cmp">${cols(3)}<tr><th>Ядро</th>${th}</tr>
      ${D.cores.map(c => { const u = pa && PR(pa)[c] && PR(pa)[c][k][n], w = pb && PR(pb)[c] && PR(pb)[c][k][n];
        return `<tr${c === core ? ` class="sel"` : ""}><td>${esc(c)}</td>${cell(u, w)}${cell(w, u)}${dPct(u, w)}</tr>`; }).join("")}</table>` : "";
    const a = xa || {}, b = xb || {};
    const better = (u, w, lowIsGood) => u == null || w == null || u === w ? ["", ""] : (lowIsGood ? w < u : w > u) ? ["", "good"] : ["good", ""];
    const line = (label, key, f = v => v ?? "—", low) => {
      const [ca, cb] = low === undefined ? ["", ""] : better(a[key], b[key], low);
      return `<tr><td>${label}</td><td class="${ca}">${f(a[key])}</td><td class="${cb}">${f(b[key])}</td></tr>`;
    };
    const bound = boundTag;
    const flags = fs => (fs || []).map(f => `<span class="badge flagbad">${esc(FLAG[f] || f)}</span>`).join(" ") || "—";
    const pxRows = `${line("Регистры", "px_regs", undefined, true)}${line("fp16, %", "px_fp16", undefined, false)}${line("Упор (самый загруженный блок)", "px_bound", bound)}
      ${line("Динамические циклы", "looped", v => v == null ? "—" : v ? "есть: цена зависит от n" : "нет")}
      <tr><td>Флаги</td><td>${xa ? flags(a.flags) : "—"}</td><td>${xb ? flags(b.flags) : "—"}</td></tr>`;
    const vtxRows = `${line("Регистры", "vtx_regs", undefined, true)}${line("Упор (самый загруженный блок)", "vtx_bound", bound)}`;
    const chars = `<h3>Характеристики шейдера ${k === "px" ? "пикселя" : "вершины"} <span class="muted small">зелёным — у кого лучше</span></h3>
      <table class="chars"><colgroup><col style="width:${LABEL_W}px"><col><col></colgroup><tr><th></th><th>A · ${A}</th><th>B · ${B}</th></tr>
      <tr><td>Keywords варианта</td><td>${pa ? kws(pa.keywords) : "—"}</td><td>${pb ? kws(pb.keywords) : "—"}</td></tr>
      ${k === "px" ? pxRows : vtxRows}
      ${(pa && pa.error) || (pb && pb.error) ? `<tr><td>Нет цены</td><td class="bad">${esc(pa && pa.error || "")}</td><td class="bad">${esc(pb && pb.error || "")}</td></tr>` : ""}</table>`;
    return `<div class="card"><h2>Проход ${name}</h2>${verdict(pa, pb, k)}${byN}${pipesTable(xa, xb, k, n)}${allCores}${chars}</div>`;
  }).join("");
  $("#app").innerHTML = head + tools + cards;
  document.querySelectorAll("#cores button").forEach(b => b.onclick = () => { core = b.dataset.c; render(); });
  document.querySelectorAll("#ns button").forEach(b => b.onclick = () => { selN = +b.dataset.n; render(); });
  document.querySelectorAll("#apis button").forEach(b => b.onclick = () => { selApi = b.dataset.a; render(); });
  document.querySelectorAll("#parts button").forEach(b => b.onclick = () => { selPart = b.dataset.k; render(); });
}
VIEWS.materials = renderMaterials;
