"use strict";
let viewId = null, logNext = 0, logEl = null, job = null, timer = null, detailsOpen = null;
const running = () => !!(job && job.status === "running" && !viewId);

function flatPhases(j) { return j.steps.flatMap(s => s.phases.map(p => ({...p, cmd: s.cmd}))); }

function overall(j, now) {
  const el = (j.finished || now) - j.started;
  if (j.status !== "running") return {el, pct: 100};
  if (j.eta != null && !j.eta_partial && el + j.eta > 0) return {el, pct: 100 * el / (el + j.eta)};
  const ps = flatPhases(j).filter(p => p.status !== "skipped");
  let d = 0;
  for (const p of ps) d += p.status === "done" ? 1 : p.status === "running" && p.total ? (p.done || 0) / p.total : 0;
  return {el, pct: ps.length ? 100 * d / ps.length : 0};
}

function phaseInfo(p, now) {
  const unit = (PHASE_TEXT[p.id] || [])[1] || "";
  const note = p.note ? (NOTE_TEXT[p.note] || p.note) : "";
  if (p.status === "running") {
    const el = p.elapsed ?? (now - p.started);
    if (p.total) {
      const rate = p.done && el > 1 ? p.done / el : null;
      return `${p.done || 0} / ${p.total} ${unit}` + (rate ? ` · ${rate >= 1 ? rate.toFixed(rate >= 10 ? 0 : 1) + "/с" : (rate * 60).toFixed(1) + "/мин"}` : "") +
        (p.eta != null ? ` · осталось ${approx(p.eta)}` : "") + (note ? ` · ${note}` : "");
    }
    return `идёт ${dur(el)}` + (p.typical ? (el > p.typical * 1.2 ? ` · дольше обычного (обычно ${approx(p.typical)})` : ` · обычно ${approx(p.typical)}`) : "") + (note ? ` · ${note}` : "");
  }
  if (p.status === "done") return (p.total ? `${p.done ?? p.total} ${unit} · ` : "") + dur(p.seconds);
  if (p.status === "pending") return p.typical != null ? `впереди · обычно ${approx(p.typical)}` : "впереди";
  if (p.status === "skipped") return "не понадобилось" + (note ? `: ${note}` : "");
  return {failed: "ошибка — см. лог", stopped: "остановлено", cancelled: "не выполнялось"}[p.status] || "";
}

function phaseRow(p, now) {
  const ic = {done: "✓", running: '<span class="spin"></span>', pending: "○", skipped: "–", failed: "✕", stopped: "■", cancelled: "·"}[p.status] || "?";
  return `<div class="phase ${p.status}"><div class="ic ${p.status}">${ic}</div><div class="nm">${esc((PHASE_TEXT[p.id] || [p.id])[0])}</div><div class="info">${esc(phaseInfo(p, now))}</div></div>`;
}

function resultHtml(j) {
  const s = j.summary;
  if (!s) return j.report ? `<div class="actions"><button class="btn primary" data-act="open">Открыть отчёт</button></div>` : "";
  const t = s.total || 0, f = t ? s.fragment / t : 0, v = t ? s.vertex / t : 0, c = t ? s.compute / t : 0;
  const top = s.shaders.map(x => `<tr><td>${esc(x.key)}</td><td style="width:35%"><div class="sb" style="width:${(100 * x.share / (s.shaders[0].share || 1)).toFixed(1)}%"></div></td><td class="n">${pct(x.share)}</td></tr>`).join("");
  return `<div class="result">${prevHtml(s.prev)}
    <div class="muted small" style="margin-top:12px">Цена кадра на ${esc(s.main_core)} (${esc(s.api)})</div>
    <div class="total">${M(t)} млн циклов</div>
    <div class="split"><i style="width:${100 * f}%;background:var(--part-px)"></i><i style="width:${100 * v}%;background:var(--part-vx)"></i><i style="width:${100 * c}%;background:var(--part-cs)"></i></div>
    <div class="legend"><span><i style="background:var(--part-px)"></i>пиксели ${pct(f)}</span><span><i style="background:var(--part-vx)"></i>вершины ${pct(v)}</span><span><i style="background:var(--part-cs)"></i>compute ${pct(c)}</span></div>
    <div class="muted small" style="margin-top:12px">Самые дорогие шейдеры (доля кадра)</div>
    <table class="top5">${top}</table>
    ${s.unpriced_draws ? `<div class="warnline">⚠ ${s.unpriced_draws} draw без цены: шейдер не скомпилировался или замер упал. Их стоимость не входит в итог.</div>` : ""}
    ${s.unpriced_compute ? `<div class="muted small" style="margin-top:6px">Compute-шейдеры без цены (${s.unpriced_compute}): их компиляция из скрипта роняет Unity 6000.3.</div>` : ""}
    ${pixelsLine(s)}
    ${s.checked === false ? `<div class="warnline">⚠ Шейдеры не перекомпилировались (Unity был недоступен или выбран расчёт без компиляции): если шейдеры менялись после прошлого расчёта, цена может быть устаревшей.</div>` : ""}
    <div class="actions"><button class="btn primary" data-act="open">Открыть полный отчёт</button>
      <button class="btn" data-act="hotspots" title="Самые дорогие шейдеры кадра: их самые тяжёлые части и что в них">Почему тяжёлые</button>
      ${s.unpriced_draws ? `<button class="btn" data-act="retry">Повторить упавшие (${s.unpriced_draws})</button>` : ""}
      <button class="btn" data-act="folder">Папка</button></div></div>`;
}

function prevHtml(p) {
  if (!p || p.delta == null) return "";
  const c = p.delta < 0 ? "good" : p.delta > 0 ? "bad" : "";
  const sg = x => (x > 0 ? "+" : x < 0 ? "−" : "±") + M(Math.abs(x));
  const pc = p.pct == null ? "" : ` (${p.pct > 0 ? "+" : p.pct < 0 ? "−" : "±"}${Math.abs(100 * p.pct).toFixed(1)}%)`;
  return `<div class="prev"><div class="muted small">К прошлому расчёту этого снимка (${when(p.a_time)}), ${esc(p.core)}</div>
    <div class="d ${c}">${sg(p.delta)} млн${pc}</div>
    <div class="small">цена шейдеров <b>${sg(p.price)}</b> · объём <b>${sg(p.work)}</b> · состав вариантов <b>${sg(p.mix)}</b></div>
    ${p.shaders && p.shaders.length ? `<div class="small" style="margin-top:4px">${p.shaders.map(x => `${esc(x.shader)} <b class="${x.delta < 0 ? "good" : "bad"}">${sg(x.delta)}</b>`).join(" · ")}</div>` : ""}
    <div style="margin-top:8px"><button class="btn sm" data-act="compare">Подробное сравнение</button></div></div>`;
}

function pixelsLine(s) {
  // where the pixel counts came from: renderdoc is exact, diff an estimate (see the report's "Как читать")
  const pm = s.pixel_methods || {};
  const name = {renderdoc: "RenderDoc (точно)", fullscreen: "полноэкранные проходы", diff: "Frame Debugger diff (оценка)", none: "нет"};
  const txt = Object.entries(pm).map(([k, n]) => `${name[k] || k}: ${n}`).join(" · ");
  if (Object.keys(pm).some(k => k !== "renderdoc"))
    return `<div class="warnline">⚠ Не у всех draw пиксели из RenderDoc: ${esc(txt)}. Снимите кадр заново.</div>`;
  return txt ? `<div class="muted small" style="margin-top:6px">Пиксели draw: ${esc(txt)}</div>` : "";
}

function costStep(j) { return j.steps.find(s => s.cmd === "cost"); }

function renderJob(j) {
  const now = j.now || Date.now() / 1000;
  const {el, pct: p} = overall(j, now);
  const st = {running: "идёт", ok: "готово", failed: "ошибка", stopped: "остановлено"}[j.status];
  const ps = flatPhases(j).filter(x => x.status !== "skipped");
  const cur = ps.findIndex(x => x.status === "running");
  let now_ = "";
  if (j.status === "running") {
    const ph = cur >= 0 ? ps[cur] : null;
    now_ = ph ? `<div class="now">Шаг ${cur + 1} из ${ps.length}: ${esc((PHASE_TEXT[ph.id] || [ph.id])[0])} <span class="muted">· ${esc(phaseInfo(ph, now))}</span></div>
      <div class="bar thin ${ph.total ? "" : "indet"}"><i style="width:${ph.total ? (100 * (ph.done || 0) / ph.total).toFixed(1) : 0}%"></i></div>`
      : `<div class="now">Подготовка…</div>`;
  }
  const etaTxt = j.eta_partial ? (j.eta ? `не меньше ${dur(j.eta)}` : "оценка появится после первой проверки") : approx(j.eta);
  const head = `<div class="jobhead"><h2>${esc(PRESET_TEXT[j.preset] || j.preset)}</h2><span class="badge ${j.status}">${st}</span>
      <span class="muted small">${when(j.started)}</span><span style="flex:1"></span>
      ${j.status === "running" && !viewId ? `<button class="btn danger" data-act="stop">Остановить</button>` : ""}</div>
    ${now_}
    <div class="bar ${j.status === "ok" ? "ok" : j.status === "running" ? "" : "bad"}"><i style="width:${p.toFixed(1)}%"></i></div>
    <div class="meta">${j.status === "running" ? `<span>готово <b>${p.toFixed(0)}%</b></span><span>прошло <b>${dur(el)}</b></span><span>осталось <b>${esc(etaTxt)}</b></span>`
      : `<span>заняло <b>${dur(el)}</b></span>`}</div>`;
  const hint = j.status === "failed" && j.hint ? `<div class="hint">${esc(j.hint.text)}
      ${j.hint.action === "no_compile" ? `<div><button class="btn" data-act="nocompile">Посчитать по уже скомпилированным вариантам</button></div>` : ""}</div>` : "";
  const res = j.status === "ok" ? resultHtml(j) : "";
  const steps = j.steps.map(s => (j.steps.length > 1 ? `<div class="stept">${esc(STEP_TEXT[s.cmd] || s.cmd)}</div>` : "") + s.phases.map(x => phaseRow(x, now)).join("")).join("");
  if (detailsOpen === null) detailsOpen = j.status === "failed";
  const card = $("#jobCard");
  const keepLog = logEl && card.contains(logEl) ? logEl : null;
  card.innerHTML = head + hint + res + `<details class="more" id="detBox" ${detailsOpen ? "open" : ""}><summary>Подробнее: все шаги и лог</summary>${steps}<pre class="log" id="log"></pre></details>`;
  if (keepLog) $("#log").replaceWith(keepLog);
  logEl = $("#log");
  $("#detBox").addEventListener("toggle", e => { detailsOpen = e.target.open; });
  card.querySelectorAll("[data-act]").forEach(b => b.onclick = () => act(b.dataset.act, j));
}

async function act(a, j) {
  const dir = j.report_path ? j.report_path.replace(/[\\/][^\\/]+$/, "") : j.frame_dir;
  const cs = costStep(j);
  if (a === "stop" && confirm("Остановить проверку? Текущий шаг прервётся.")) await api("/api/stop", {});
  if (a === "open") openReport({name: dir.split(/[\\/]/).pop(), report: j.report, path: dir});
  if (a === "folder") api("/api/reveal", {path: dir});
  if (a === "hotspots") start("hotspots", {frame: dir});
  if (a === "compare" && j.summary && j.summary.prev) openCompare(j.summary.prev.run_a, j.summary.prev.run_b);
  if (a === "retry" && cs) start("cost", {...cs.values, retry_failed: true});
  if (a === "nocompile" && cs) start("cost", {...cs.values, no_compile: true});
}

function appendLog(lines) {
  if (!logEl || !lines.length) return;
  const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 30;
  logEl.insertAdjacentHTML("beforeend", lines.map(l => {
    const c = l.startsWith("$ ") ? "cmd" : /^(FAIL|Traceback|.*Error\b|.*failed:)/.test(l) ? "fail" : l.startsWith("-> ") ? "arrow" : "";
    return c ? `<span class="${c}">${esc(l)}</span>` : esc(l);
  }).join("\n") + "\n");
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
}

function pill(j) {
  const el = $("#tabPill");
  if (j && j.status === "running") {
    const {pct: p} = overall(j, j.now);
    el.innerHTML = `<span class="pill run">${p.toFixed(0)}%</span>`;
    document.title = `${p.toFixed(0)}% · ParetoGPU`;
  } else {
    el.innerHTML = !j || j.status === "stopped" ? "" : j.status === "ok" ? `<span class="pill ok">✓</span>` : `<span class="pill bad">✕</span>`;
    document.title = "ParetoGPU";
  }
}

async function poll() {
  clearTimeout(timer);
  try {
    const q = viewId ? `id=${encodeURIComponent(viewId)}&since=${logNext}` : `since=${logNext}`;
    const j = (await api("/api/job?" + q)).job;
    if (j) {
      if (job && job.id !== j.id) { job = null; logNext = 0; logEl = null; detailsOpen = null; return poll(); }
      const finished = job && job.status === "running" && j.status !== "running";
      if (finished && j.status === "failed") detailsOpen = true;  // the log is what explains a failure
      job = j;
      renderJob(j);
      appendLog(j.log);
      logNext = j.log_next;
      if (!viewId) pill(j);
      if (finished) { loadHistory(); OPT = await api("/api/options"); }
    }
    updateRun();
  } catch (e) { /* the server restarts: try again */ }
  timer = setTimeout(poll, job && job.status === "running" ? 700 : 3000);
}

async function loadHistory() {
  const r = await api("/api/jobs");
  const rows = r.jobs.filter(x => x.status !== "running");
  const name = {ok: "готово", failed: "ошибка", stopped: "остановлено"};
  $("#hist").innerHTML = rows.length ? rows.slice(0, 15).map(x => `<tr class="click" data-id="${esc(x.id)}"><td>${when(x.started)}</td>
    <td>${esc(PRESET_TEXT[x.preset] || x.preset)}</td><td><span class="badge ${x.status}">${name[x.status] || x.status}</span></td>
    <td class="muted">${x.finished ? dur(x.finished - x.started) : ""}</td></tr>`).join("")
    : `<tr><td class="muted">Проверок ещё не было</td></tr>`;
  document.querySelectorAll("#hist tr.click").forEach(tr => tr.onclick = () => {
    viewId = tr.dataset.id; logNext = 0; logEl = null; job = null; detailsOpen = null; $("#jobCard").innerHTML = ""; poll();
    if (!$("#backCur")) $("#jobCard").insertAdjacentHTML("beforebegin", `<button class="btn" id="backCur" style="margin-bottom:8px">← К текущей проверке</button>`);
    $("#backCur").onclick = () => { viewId = null; logNext = 0; logEl = null; job = null; detailsOpen = null; $("#backCur").remove(); poll(); };
  });
}

/* ---------- reports ---------- */
