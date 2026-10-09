let ablMetric = null, ablPart = {}, ablCore = null;
const METRICS = {price: ["Экономия цены", "Насколько упадёт цена шейдера (циклы самого загруженного блока), если убрать часть"],
  arith: ["A", "Арифметика: FMA, CVT, SFU"], ls: ["LS", "Память: буферы, спилл регистров"], v: ["V", "Varyings"], t: ["T", "Текстуры"]};
const mInc = (row, m) => !row || row.error ? null : m === "price" ? row.price : (row.incl || {})[m];
const mSelf = (row, m) => !row || row.error ? null : m === "price" ? row.self_price : (row.self || {})[m];

function singlePipes(x, k, n) {
  const g = x && x[k + "_pipes"];
  if (!g) return "";
  const l = g.longest[n], top = Math.max(...["arith", "ls", "v", "t"].filter(p => l[p] != null).map(p => l[p]));
  const rows = PIPES.filter(([key]) => [l, g.shortest, g.total].some(c => c && c[key] != null)).map(([key, label, sub]) => {
    const hot = ["arith", "ls", "v", "t"].includes(key) && Math.abs(l[key] - top) < 1e-6;
    return `<tr${sub ? ` class="subp"` : ""}><td title="${esc(PIPE[key][2])}" style="cursor:help">${sub ? "&nbsp;&nbsp;&nbsp;" : ""}${label}</td>
      <td class="n">${hot ? `<b class="hot" title="Самый загруженный блок: его циклы и есть цена (упор)">${c1(l[key])} ●</b>` : c1(l[key])}</td>
      <td class="n gl">${c1(g.shortest && g.shortest[key])}</td><td class="n gl">${c1(g.total && g.total[key])}</td></tr>`;
  }).join("");
  return `<table class="cmp">${cols(3)}<tr><th>Конвейер</th><th class="n" title="Самый дорогой путь: каждый if идёт по более дорогой ветке. По нему считается цена">Длинный путь${byNs(x, k) ? ` (n = ${n})` : ""}</th>
    <th class="n gl" title="Все if идут по дешёвой ветке">Короткий</th><th class="n gl" title="Каждая инструкция один раз: объём кода, а не время">Всего</th></tr>${rows}</table>`;
}

const ABL = {}, ablMove = {}, ablCode = {};
// the ablation of one shader stage: its costliest parts, the candidates for the vertex shader, its code with heat
function ablationBlock(key, a, opts = {}) {
  if (!a) return "";
  if (a.error) return `<div class="warn">Разбор по строкам: ${esc(a.error)}</div>`;
  ABL[key] = a;
  const cores = Object.keys(a.by_core);
  const c = cores.includes(core) ? core : cores[0];
  const x = a.by_core[c];
  if (x.base.error) return `<div class="warn">Разбор по строкам на ${esc(c)}: ${esc(x.base.error)}</div>`;
  const ids = Object.keys(a.statements);
  const roots = ids.filter(id => a.statements[id].parent == null && x.stmts[id] && !x.stmts[id].error);
  let m = ablMetric;
  if (!m) m = roots.some(id => (x.stmts[id].price || 0) > 0) ? "price" : "arith";
  const baseV = m === "price" ? x.base.price : x.base.pipes[m];
  const share = v => baseV ? p0(Math.max(0, v) / baseV) : "";
  const sel = ablPart[key];
  const sub = id => [id, ...(a.statements[id].children || []).flatMap(sub)];
  const inPart = new Set(sel != null ? sub(String(sel)).map(String) : []);
  const mv = (a.vertex_candidates || []).find(v => String(v.id) === String(ablMove[key]));
  const inMove = new Set(mv ? mv.chain.map(String) : []);
  const byM = (u, w) => (mInc(x.stmts[w], m) || 0) - (mInc(x.stmts[u], m) || 0) || ((x.stmts[w].incl || {}).arith || 0) - ((x.stmts[u].incl || {}).arith || 0);
  roots.sort(byM);
  const st = id => a.statements[id];
  const line = id => st(id).line + 1;
  const loopMark = id => st(id).loop ? ` <span class="lp" title="Строка в динамическом цикле: её цена — за n = ${a.n} итераций">↻</span>` : "";
  const pipesOf = row => ["arith", "ls", "v", "t"].filter(p => Math.abs(row.incl[p] || 0) >= 0.01).map(p => `${PIPE[p][0]} ${c1(row.incl[p])}`).join(" · ");
  const partRow = (id, kid) => { const row = x.stmts[id], v = mInc(row, m), n = sub(id).length, ex = st(id).explain;
    return `<tr class="${kid ? "kid" : ""} ${String(sel) === String(id) ? "on" : ""}" data-part="${id}" data-key="${key}"><td class="n">${line(id)}</td>
      <td class="code" title="${esc(st(id).text)}">${kid ? "↳ " : ""}${esc(st(id).text)}</td>
      <td class="small" title="${esc(ex ? ex.text : "")}">${esc(ex ? ex.text : "")}</td><td class="n">${n > 1 ? n : ""}</td>
      <td class="n"><b>${c1(v)}</b>${loopMark(id)}</td><td class="n">${share(v)}</td><td class="small muted">${pipesOf(row)}</td></tr>`; };
  const top = opts.top || 15;
  const rows = roots.slice(0, top).map(id => partRow(id) + (String(sel) === String(id)
    ? (st(id).children || []).filter(k => x.stmts[k] && !x.stmts[k].error).sort(byM).slice(0, 12).map(k => partRow(k, true)).join("") : "")).join("");
  const metricSeg = `<div class="seg" data-metrics="1">${Object.entries(METRICS).filter(([k]) => k === "price" || x.base.pipes[k] != null)
    .map(([k, [t, tip]]) => `<button data-m="${k}" title="${esc(tip)}" class="${k === m ? "on" : ""}">${t}</button>`).join("")}</div>`;
  // candidates for the vertex shader: what moving each saves is its own ablation (the same new varying)
  const cands = (a.vertex_candidates || []).map(v => ({...v, row: x.stmts[v.id]})).sort((u, w) => (mInc(w.row, m) || 0) - (mInc(u.row, m) || 0));
  const moves = cands.length ? `<h3>Можно посчитать в вертексе <span class="muted small" title="Значения, аффинные по varying с коэффициентами из свойств: их можно точно посчитать в вершинном шейдере и передать через Custom Interpolator">${cands.length} · ${refLink("vertex-move", "что это")}</span></h3>
    <table class="cmp parts">${`<colgroup><col style="width:56px"><col style="width:400px"><col style="width:250px"><col style="width:70px"><col style="width:90px"><col style="width:70px"><col style="width:70px"></colgroup>`}
      <tr><th class="n">Строка</th><th title="Значение, которое станет новым varying; клик — его цепочка в коде">Значение</th><th>Что это</th>
      <th class="n" title="Сколько компонент займёт в varyings">Компонент</th>
      <th class="n" title="${esc(METRICS[m][1])}: столько сэкономит пиксельный шейдер (с учётом нового varying)">${esc(METRICS[m][0])}</th>
      <th class="n" title="Доля от цены шейдера">Доля</th><th class="n" title="Строк в цепочке, которая переедет в вертекс">Строк</th></tr>
      ${cands.slice(0, top).map(v => { const val = mInc(v.row, m);
        return `<tr class="${mv && mv.id === v.id ? "on" : ""}" data-move="${v.id}" data-key="${key}"><td class="n">${v.line + 1}</td>
        <td class="code" title="${esc(v.text)}">${esc(v.text)}</td><td class="small">${esc(v.label)}</td><td class="n">${v.width}</td>
        <td class="n ${val > 0 ? "good" : "muted"}"><b>${c1(val)}</b></td><td class="n">${val > 0 ? share(val) : ""}</td><td class="n">${v.chain.length}</td></tr>`; }).join("")}</table>
    ${a.varyings ? `<div class="small muted" title="Mali интерполирует varyings по 4 компоненты; у vec3 и vec2 свободные компоненты можно занять без нового слота">varyings сейчас: ${a.varyings.varyings}, свободных компонент в них: ${a.varyings.free_components}</div>` : ""}` : "";
  const showCode = opts.code !== false || ablCode[key];
  const maxSelf = Math.max(...ids.map(id => mSelf(x.stmts[id], m) || 0), 1e-9);
  const code = !showCode ? "" : a.lines.map(l => { const id = l.stmt, row = id != null ? x.stmts[id] : null, v = mSelf(row, m);
    const w = v ? Math.max(2, 100 * v / maxSelf) : 0;
    const cl = id != null && inPart.has(String(id)) ? (String(id) === String(sel) ? "sel" : "part") : id != null && inMove.has(String(id)) ? "mv" : "";
    return `<tr id="L-${key}-${l.no}" class="${cl}"${id != null ? ` data-part="${id}" data-key="${key}" style="cursor:pointer"` : ""}>
      <td class="no">${l.no}</td><td class="heat">${w ? `<div class="hb ${v < maxSelf / 4 ? "lo" : ""}" style="width:${w}%"></div>` : ""}</td>
      <td class="val">${v ? c1(v) : ""}</td><td class="code">${esc(l.text)}</td></tr>`; }).join("");
  return `<div class="abl"><h3>Самые тяжёлые части <span class="muted small">${esc(c)}, GLES, n = ${a.n} · ${refLink("ablation", "как считается")}</span></h3>
    <div class="tools" style="margin:0 0 6px"><span class="small muted" title="По какому блоку сортировать части и красить строки">Сортировать по</span>${metricSeg}
      <span class="small muted" title="Цена этого шейдера в GLES на ${esc(c)} при n = ${a.n}">из ${c1(baseV)} ${m === "price" ? "циклов цены" : "циклов " + PIPE[m][0]}</span></div>
    <table class="cmp parts">${`<colgroup><col style="width:56px"><col style="width:400px"><col style="width:300px"><col style="width:50px"><col style="width:80px"><col style="width:60px"><col style="width:220px"></colgroup>`}
      <tr><th class="n" title="Строка в коде шейдера">Строка</th><th title="Последняя строка части; клик — её строки в коде и самые дорогие подчасти">Часть</th>
      <th title="Что делает часть: выборки текстур, чтение буферов, sin, деления… (по всем её строкам)">Что внутри</th>
      <th class="n" title="Сколько строк кода в части: строка и всё, что без неё стало бы мёртвым кодом">Строк</th>
      <th class="n" title="${esc(METRICS[m][1])}: насколько упадёт, если убрать часть">${esc(METRICS[m][0])}</th><th class="n" title="Доля от цены шейдера">Доля</th>
      <th title="Насколько упадёт каждый блок, если убрать часть">По блокам</th></tr>${rows}</table>
    ${moves}
    <h3>Код <span class="muted small" title="Полоса и число — собственная цена строки: без строк её части">собственная цена строки · клик — её часть</span></h3>
    ${showCode ? `<div class="codev" id="code-${key}"><table>${code}</table></div>` : `<button class="btn" data-code="${key}">Показать код (${a.lines.length} строк)</button>`}</div>`;
}

// clicks of the ablation blocks: a part, a vertex candidate, the code, the metric
function bindAbl() {
  const scrollTo = (kk, line) => { const row = line && document.getElementById(`L-${kk}-${line}`);
    if (row) { const box = row.closest(".codev"); box.scrollTop = row.offsetTop - box.clientHeight / 2; } };
  document.querySelectorAll("[data-part]").forEach(el => el.onclick = () => {
    const kk = el.dataset.key, id = el.dataset.part;
    ablPart[kk] = String(ablPart[kk]) === id && el.closest(".parts") ? null : id;
    ablMove[kk] = null; ablCode[kk] = true;
    render();
    const a = ABL[kk];
    scrollTo(kk, ablPart[kk] != null && a.statements[ablPart[kk]] ? a.statements[ablPart[kk]].line + 1 : null);
  });
  document.querySelectorAll("[data-move]").forEach(el => el.onclick = () => {
    const kk = el.dataset.key, id = el.dataset.move;
    ablMove[kk] = String(ablMove[kk]) === id ? null : id;
    ablPart[kk] = null; ablCode[kk] = true;
    render();
    const v = (ABL[kk].vertex_candidates || []).find(c => String(c.id) === id);
    scrollTo(kk, v && ablMove[kk] != null ? v.line + 1 : null);
  });
  document.querySelectorAll("[data-code]").forEach(el => el.onclick = () => { ablCode[el.dataset.code] = true; render(); });
  document.querySelectorAll("[data-metrics] button").forEach(b => b.onclick = () => { ablMetric = b.dataset.m; render(); });
}

/* ---------- why the heaviest shaders of a frame are heavy (python -m paretogpu hotspots) ---------- */
