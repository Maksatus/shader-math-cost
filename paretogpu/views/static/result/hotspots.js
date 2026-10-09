function renderHotspots() {
  core = D.core;
  const STAGE_RU = {fragment: "Пиксельный шейдер", vertex: "Вершинный шейдер"};
  const topPart = a => { if (!a || a.error) return null;
    const x = a.by_core[D.core]; if (!x || x.base.error) return null;
    const ids = Object.keys(a.statements).filter(id => x.stmts[id] && !x.stmts[id].error);
    // the costliest part below the shader's output: a root that is not almost the whole shader
    const roots = ids.filter(id => a.statements[id].parent == null).sort((u, w) => (x.stmts[w].price || 0) - (x.stmts[u].price || 0));
    const kids = id => (a.statements[id].children || []).filter(k => x.stmts[k] && !x.stmts[k].error).sort((u, w) => (x.stmts[w].price || 0) - (x.stmts[u].price || 0));
    let id = roots[0];
    while (id != null && x.stmts[id].price > 0.75 * x.base.price && kids(id).length && x.stmts[kids(id)[0]].price > 0.25 * x.base.price) id = kids(id)[0];
    return id == null ? null : {text: a.statements[id].explain.text, line: a.statements[id].line + 1, price: x.stmts[id].price, base: x.base.price};
  };
  const head = `<div class="card"><h1>Почему тяжёлые шейдеры</h1>
    <div class="mode small muted">Снимок <b>${esc(D.snapshot)}</b> · ${esc(D.core)} · цены кадра — ${esc(D.api || "")}, разбор строк — GLES · malioc ${esc(D.malioc || "?")} · расчёт ${when(D.computed_at)}</div>
    ${D.warnings.length ? `<div class="warn"><ul>${D.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
    <p class="small" style="margin:10px 0 0">${refLink("hotspots", "Как читать")} · ${costHelp}</p></div>`;
  const overview = `<div class="card"><h2>Самые дорогие шейдеры кадра</h2><table class="cmp parts">
    <colgroup><col style="width:40px"><col style="width:420px"><col style="width:80px"><col style="width:480px"><col style="width:90px"></colgroup>
    <tr><th class="n">#</th><th>Шейдер · проход</th><th class="n" title="Доля в цене кадра на ${esc(D.core)}">% кадра</th>
    <th title="Самая дорогая часть пиксельного шейдера ниже его выхода и что в ней">Что тяжёлое</th><th class="n" title="Кандидатов на перенос в вертекс">В вертекс</th></tr>
    ${D.shaders.map((it, i) => { const t = topPart((it.ablation || {}).fragment) || topPart((it.ablation || {}).vertex);
      const nm = ((it.ablation || {}).fragment || {}).vertex_candidates;
      return `<tr data-go="hs-${i}"><td class="n">${i + 1}</td><td class="code" title="${esc(it.variant)}">${esc(it.shader)} · ${esc(it.pass || "")}</td>
        <td class="n"><b>${pct(it.share || 0, 1).replace("+", "")}</b></td>
        <td class="small">${t ? `${esc(t.text)} <span class="muted">· строка ${t.line}, ${p0(Math.max(0, t.price) / (t.base || 1))} цены</span>` : `<span class="muted">${esc((((it.ablation || {}).fragment || {}).error) || "—")}</span>`}</td>
        <td class="n">${nm && nm.length ? nm.length : ""}</td></tr>`; }).join("")}</table></div>`;
  const cards = D.shaders.map((it, i) => {
    const pxPrice = it.pixels ? it.fragment / it.pixels : null;
    return `<div class="card" id="hs-${i}"><h2>#${i + 1} ${esc(it.shader)} <span class="muted">· ${esc(it.pass || "")}</span>
        <span class="badge" style="background:var(--chip)">${pct(it.share || 0, 1).replace("+", "")} кадра</span></h2>
      <div class="small">${kws(it.keywords || [])}</div>
      <div class="small muted" style="margin-top:4px">${num(it.events)} событий · ${num(it.pixels)} пикс. · ${num(it.vertices)} верш.${pxPrice != null ? ` · в кадре ${c1(pxPrice)} цикл./пикс. (${esc(D.api || "")})` : ""}</div>
      ${Object.entries(it.ablation || {}).map(([stage, a]) => `<h3 style="margin-top:14px">${STAGE_RU[stage] || stage}</h3>${ablationBlock(`${i}:${stage}`, a, {code: false, top: 8})}`).join("")}</div>`;
  }).join("");
  $("#app").innerHTML = head + overview + cards;
  document.querySelectorAll("[data-go]").forEach(el => { el.style.cursor = "pointer"; el.onclick = () => document.getElementById(el.dataset.go).scrollIntoView({behavior: "smooth"}); });
  bindAbl();
}

VIEWS.hotspots = renderHotspots;
