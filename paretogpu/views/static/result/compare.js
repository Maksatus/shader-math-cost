function renderCompare() {
  const x = D.by_core[core];
  const head = `<div class="card"><h1>Сравнение кадров</h1>
    <div class="ab">${side(D.a, "A")}<div class="arrow">→</div>${side(D.b, "B")}</div>
    <div class="mode">${D.same_frame
      ? "<b>Тот же снимок, посчитанный дважды.</b> Пиксели и вершины одинаковые, поэтому вся разница — это цена шейдеров."
      : "<b>Два разных снимка.</b> Разница складывается из цены шейдеров и объёма работы: ракурс, сцена, LOD, анимации. Ниже они разделены."}</div>
    ${D.warnings.length ? `<div class="warn"><ul>${D.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
    <p class="small" style="margin:10px 0 0">${refLink("compare-frames", "Как читать сравнение")}</p></div>`;
  if (!x) { $("#app").innerHTML = head; return; }
  const cores = D.cores.length > 1 ? `<div class="seg" id="cores">${D.cores.map(c => `<button data-c="${esc(c)}" class="${c === core ? "on" : ""}">${esc(c)}${c === D.main_core ? " ★" : ""}</button>`).join("")}</div>` : `<b>${esc(core)}</b>`;
  const maxPart = Math.max(Math.abs(x.price), Math.abs(x.work), Math.abs(x.mix), 1);
  const part = (name, v, note) => `<div>${name}<div class="small muted">${note}</div></div><div class="dbar"><span class="mid"></span><i style="${v < 0 ? `right:50%;width:${50 * Math.abs(v) / maxPart}%;background:var(--cheap)` : `left:50%;width:${50 * Math.abs(v) / maxPart}%;background:var(--heavy)`}"></i></div><div class="num ${cls(v)}"><b>${sM(v)}</b> млн</div>`;
  const sp = (s, k) => s ? 100 * x.split[k][s] / Math.max(x.split.fragment[s] + x.split.vertex[s] + x.split.compute[s], 1) : 0;
  const splitBar = s => `<div class="split2"><i style="width:${sp(s, "fragment")}%;background:var(--part-px)"></i><i style="width:${sp(s, "vertex")}%;background:var(--part-vx)"></i><i style="width:${sp(s, "compute")}%;background:var(--part-cs)"></i></div>`;
  const summary = `<div class="card"><div class="tools"><h2 style="margin:0">Итог кадра</h2><span style="flex:1"></span><span class="small muted">Ядро</span>${cores}</div>
    <div class="kpis">
      <div class="kpi"><div class="k">A, было</div><div class="v">${M(x.a)}</div><div class="s">млн циклов</div></div>
      <div class="kpi"><div class="k">B, стало</div><div class="v">${M(x.b)}</div><div class="s">млн циклов</div></div>
      <div class="kpi"><div class="k">Разница</div><div class="v ${cls(x.delta)}">${sM(x.delta)}</div><div class="s ${cls(x.delta)}">${pct(x.delta, x.a)} ${x.delta < 0 ? "дешевле" : x.delta > 0 ? "дороже" : ""}</div></div>
    </div>
    <div class="decomp">${part("Цена шейдеров", x.price, "сами шейдеры")}${part("Объём работы", x.work, "пиксели, вершины, потоки")}${part("Состав вариантов", x.mix, "новые и пропавшие")}</div>
    <div style="display:grid;grid-template-columns:30px 1fr;gap:4px 8px;align-items:center;margin-top:14px;font-size:12px">
      <b>A</b>${splitBar("a")}<b>B</b>${splitBar("b")}</div>
    <div class="legend"><span><i style="background:var(--part-px)"></i>пиксели ${M(x.split.fragment.a)} → ${M(x.split.fragment.b)}</span>
      <span><i style="background:var(--part-vx)"></i>вершины ${M(x.split.vertex.a)} → ${M(x.split.vertex.b)}</span>
      <span><i style="background:var(--part-cs)"></i>compute ${M(x.split.compute.a)} → ${M(x.split.compute.b)}</span></div></div>`;
  const smax = Math.max(...x.stages.map(s => Math.abs(s.delta)), 1);
  const stages = `<div class="card"><h2>По стадиям кадра</h2><table><tr><th>Стадия</th><th class="n">A</th><th class="n">B</th><th class="n">Разница</th><th class="n"></th><th></th></tr>
    ${x.stages.map(s => `<tr><td>${esc(STAGE[s.stage] || s.stage)}</td><td class="n">${M(s.a)}</td><td class="n">${M(s.b)}</td>
      <td class="n ${cls(s.delta)}">${sM(s.delta)}</td><td class="n ${cls(s.delta)}">${pct(s.delta, s.a)}</td><td>${bar(s.delta, smax)}</td></tr>`).join("")}</table></div>`;
  const small = s => Math.abs(s.delta) < 0.001 * Math.max(x.a, x.b);
  let rows = x.shaders.filter(s => !query || s.shader.toLowerCase().includes(query));
  const hidden = hideSmall ? rows.filter(small).length : 0;
  if (hideSmall) rows = rows.filter(s => !small(s));
  const dmax = Math.max(...rows.map(s => Math.abs(s.delta)), 1);
  const tbl = rows.map((s, i) => {
    const id = s.shader;
    const badges = (s.status === "new" ? `<span class="badge new">новый</span>` : s.status === "gone" ? `<span class="badge gone">пропал</span>` : s.status === "mixed" ? `<span class="badge mixed">варианты поменялись</span>` : "")
      + s.flags_added.map(f => `<span class="badge flagbad">+ ${esc(FLAG[f] || f)}</span>`).join("") + s.flags_removed.map(f => `<span class="badge flaggood">− ${esc(FLAG[f] || f)}</span>`).join("");
    return `<tr class="row ${small(s) ? "small" : ""}" data-id="${esc(id)}"><td>${open.has(id) ? "▾" : "▸"} <b>${esc(s.shader)}</b>${badges}</td>
      <td class="n">${M(s.a)}</td><td class="n">${M(s.b)}</td><td class="n ${cls(s.delta)}"><b>${sM(s.delta)}</b></td><td class="n ${cls(s.delta)}">${pct(s.delta, s.a)}</td>
      <td>${bar(s.delta, dmax)}</td><td class="n ${cls(s.price)}">${Math.abs(s.price) > 1 ? sM(s.price) : "·"}</td><td class="n ${cls(s.work)}">${Math.abs(s.work) > 1 ? sM(s.work) : "·"}</td>
      <td class="n ${cls(s.mix)}">${Math.abs(s.mix) > 1 ? sM(s.mix) : "·"}</td></tr>` + (open.has(id) ? `<tr class="det"><td colspan="9">${details(s)}</td></tr>` : "");
  }).join("");
  const shaders = `<div class="card"><div class="tools"><h2 style="margin:0">По шейдерам</h2><span class="small muted">клик по строке — варианты и события</span><span style="flex:1"></span>
      <input type="search" id="q" placeholder="Найти шейдер…" value="${esc(query)}">
      <label class="small"><input type="checkbox" id="hs" ${hideSmall ? "checked" : ""}> скрыть изменения меньше 0.1% кадра${hidden ? ` (${hidden})` : ""}</label></div>
    <table><tr><th>Шейдер</th><th class="n">A</th><th class="n">B</th><th class="n">Разница</th><th class="n"></th><th></th><th class="n">цена</th><th class="n">объём</th><th class="n">состав</th></tr>
    ${tbl || `<tr><td colspan="9" class="muted">Нет изменений${hidden ? " крупнее 0.1% кадра" : ""}.</td></tr>`}</table></div>`;
  $("#app").innerHTML = head + summary + stages + shaders;
  document.querySelectorAll("#cores button").forEach(b => b.onclick = () => { core = b.dataset.c; render(); });
  const q = $("#q");
  q.oninput = () => { query = q.value.toLowerCase(); const p = q.selectionStart; render(); const n = $("#q"); n.focus(); n.setSelectionRange(p, p); };
  $("#hs").onchange = e => { hideSmall = e.target.checked; render(); };
  document.querySelectorAll("tr.row").forEach(tr => tr.onclick = () => { const id = tr.dataset.id; open.has(id) ? open.delete(id) : open.add(id); render(); });
}

function details(s) {
  const same = s.variants.filter(v => v.status === "both" && Math.abs(v.delta) < 1);
  const shown = s.variants.filter(v => !same.includes(v));
  return (shown.length ? shown : same).map(v => {
    const st = v.status === "new" ? `<span class="badge new">только в B</span>` : v.status === "gone" ? `<span class="badge gone">только в A</span>` : "";
    const a = v.stats_a || {}, b = v.stats_b || {};
    const ch = (k, label, f = x => x ?? "—") => (a[k] != null || b[k] != null) ? `<div><span class="k">${label}:</span> ${v.status === "both" ? `${f(a[k])} → ${f(b[k])}` : f((v.stats_b || v.stats_a)[k])}</div>` : "";
    const parts = Object.entries(v.stages).map(([k, p]) => `<div><span class="k">${PART[k]}:</span> ${v.status === "both"
      ? `${num(p.work_a)} → ${num(p.work_b)} ${UNIT[k]} × ${pr(p.price_a)} → ${pr(p.price_b)} цикл.` : `${num(p.work_a ?? p.work_b)} ${UNIT[k]} × ${pr(p.price_a ?? p.price_b)} цикл.`}</div>`).join("");
    const flags = v.status === "both" ? v.flags_added.map(f => `<span class="badge flagbad">+ ${esc(FLAG[f] || f)}</span>`).join("") + v.flags_removed.map(f => `<span class="badge flaggood">− ${esc(FLAG[f] || f)}</span>`).join("")
      : ((v.stats_b || v.stats_a).flags || []).map(f => `<span class="badge gone">${esc(FLAG[f] || f)}</span>`).join("");
    const evs = v.events.map(e => `<tr><td>${esc(e.object || "—")}</td><td>${esc(STAGE[e.stage] || e.stage || "")}</td>
      <td class="n">${e.index_a != null ? "#" + (e.index_a + 1) : "—"} / ${e.index_b != null ? "#" + (e.index_b + 1) : "—"}</td>
      <td class="n">${num(e.pixels_a)} → ${num(e.pixels_b)}</td><td class="n">${e.a == null ? "—" : M(e.a)}</td><td class="n">${e.b == null ? "—" : M(e.b)}</td>
      <td class="n ${cls(e.delta)}">${sM(e.delta)}</td></tr>`).join("");
    return `<div class="var"><div><b>${esc(v.pass || "")}</b> ${st} <span class="${cls(v.delta)}" style="float:right"><b>${sM(v.delta)}</b> млн (${M(v.a)} → ${M(v.b)})</span></div>
      <div class="kw">${esc(v.keywords.join(" ") || "без keywords")}</div>
      ${v.status === "both" ? `<div class="small" style="margin-top:4px">цена <b class="${cls(v.price)}">${sM(v.price)}</b> · объём <b class="${cls(v.work)}">${sM(v.work)}</b></div>` : ""}
      <div class="grid">${parts}${ch("px_regs", "регистры пикселя")}${ch("vtx_regs", "регистры вершины")}${ch("px_fp16", "fp16 пикселя, %")}
        ${ch("px_bound", "упор пикселя", boundTag)}${ch("px_path", "путь пикселя")}</div>
      ${flags ? `<div style="margin-top:6px">${flags}</div>` : ""}
      ${evs ? `<details style="margin-top:6px"><summary>События (${v.events_count}${v.events_count > v.events.length ? `, показаны ${v.events.length} с наибольшей разницей` : ""})</summary>
        <table style="margin-top:4px"><tr><th>Объект</th><th>Стадия</th><th class="n">Номер A / B</th><th class="n">Пиксели</th><th class="n">A</th><th class="n">B</th><th class="n">Разница</th></tr>${evs}</table></details>` : ""}</div>`;
  }).join("") + (shown.length && same.length ? `<div class="small muted">Ещё ${same.length} ${same.length === 1 ? "вариант" : "вариантов"} без изменений: ${same.map(v => esc(v.pass || v.variant)).join(", ")}</div>` : "");
}
/* ---------- one material: its shader and what every line costs (python -m paretogpu matshader) ---------- */
VIEWS.compare = renderCompare;
