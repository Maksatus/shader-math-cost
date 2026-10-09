function renderMaterial() {
  const M_ = D.m, n = String(selN), k = selPart;
  const stage = k === "px" ? "fragment" : "vertex";
  const looped = M_.passes.some(p => Object.values(PR(p)).some(x => x.looped));
  const head = `<div class="card"><h1>Материал</h1>
    <div class="side"><b>${esc(M_.material)}</b>
      <div class="small muted" style="font-family:var(--mono);word-break:break-all">${esc(M_.path)}</div>
      <div class="small" style="margin-top:4px">${esc(M_.shader)}</div><div class="small" style="margin-top:4px">${kws(M_.keywords)}</div></div>
    <div class="mode small muted">Расчёт ${when(D.computed_at)} · malioc ${esc(D.malioc || "?")} · ${esc(D.project)}</div>
    ${D.warnings.length ? `<div class="warn"><ul>${D.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
    <p class="small" style="margin:10px 0 0">${refLink("ablation", "Как читать")} · ${costHelp}</p></div>`;
  if (!D.cores.length) { $("#app").innerHTML = head; return; }
  const cores = D.cores.length > 1 ? `<div class="seg" id="cores">${D.cores.map(c => `<button data-c="${esc(c)}" class="${c === core ? "on" : ""}">${esc(c)}${c === D.main_core ? " ★" : ""}</button>`).join("")}</div>` : `<b>${esc(core)}</b>`;
  const parts = `<div class="seg" id="parts">${Object.entries(PARTS).map(([kk, [t]]) => `<button data-k="${kk}" class="${kk === selPart ? "on" : ""}">${t}</button>`).join("")}</div>`;
  const apis = (D.apis || [D.api]).length > 1 ? `<div class="seg" id="apis">${D.apis.map(x => `<button data-a="${esc(x)}" class="${x === selApi ? "on" : ""}">${API_NAME[x] || esc(x)}</button>`).join("")}</div>` : "";
  const nsel = looped ? `<span class="small muted">n циклов</span><div class="seg" id="ns">${D.ns.map(v => `<button data-n="${v}" class="${v === selN ? "on" : ""}">${v}</button>`).join("")}</div>` : "";
  const tools = `<div class="card" style="padding:10px 16px"><div class="tools" style="margin:0">${parts}${apis}<span class="small muted" style="margin-left:8px">Ядро</span>${cores}<span style="flex:1"></span>${nsel}</div></div>`;
  const cards = M_.passes.map((p, pi) => {
    const x = PR(p)[core];
    const a = (p.ablation || {})[stage];
    const price = x ? x[k][n] : null;
    const bound = boundTag(x && x[k + "_bound"]);
    const flags = (x && x.flags || []).map(f => `<span class="badge flagbad">${esc(FLAG[f] || f)}</span>`).join(" ");
    const facts = x ? `<div class="kpis"><div class="kpi"><div class="k">${PARTS[k][0]}</div><div class="v">${c1(price)}</div>
        <div class="s">циклов на ${PARTS[k][1]} · ${API_NAME[selApi] || esc(selApi)}${byNs(x, k) ? `, n = ${n}` : ""}</div></div>
      <div class="kpi"><div class="k">Упор</div><div class="v" style="font-size:16px;margin-top:6px">${bound}</div></div>
      <div class="kpi"><div class="k">Регистры</div><div class="v">${c1(k === "px" ? x.px_regs : x.vtx_regs)}</div>${k === "px" && x.px_fp16 != null ? `<div class="s">fp16 ${x.px_fp16}%</div>` : ""}</div></div>
      ${flags ? `<div style="margin-top:8px">${flags}</div>` : ""}` : `<div class="warn">${esc(p.error || "нет цены на этом ядре")}</div>`;
    return `<div class="card"><h2>Проход ${esc(p.pass)}</h2>${facts}
      <div class="small" style="margin-top:8px"><span class="muted" title="Keywords материала + глобальные keywords пайплайна для прохода">Keywords варианта:</span> ${kws(p.keywords)}</div>
      <h3>По конвейерам <span class="muted small">${esc(core)}, ${API_NAME[selApi] || esc(selApi)}</span></h3>${singlePipes(x, k, n)}
      ${ablationBlock(`${pi}:${stage}`, a)}</div>`;
  }).join("");
  $("#app").innerHTML = head + tools + cards;
  document.querySelectorAll("#cores button").forEach(b => b.onclick = () => { core = b.dataset.c; render(); });
  document.querySelectorAll("#ns button").forEach(b => b.onclick = () => { selN = +b.dataset.n; render(); });
  document.querySelectorAll("#parts button").forEach(b => b.onclick = () => { selPart = b.dataset.k; render(); });
  document.querySelectorAll("#apis button").forEach(b => b.onclick = () => { selApi = b.dataset.a; render(); });
  bindAbl();
}

/* ---------- two materials (python -m paretogpu matcompare) ---------- */
VIEWS.material = renderMaterial;
