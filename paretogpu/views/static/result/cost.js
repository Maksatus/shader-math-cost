function renderCost() {
  $("#app").innerHTML = `<header>
    <h1 id="title"></h1>
    <p class="sub" id="meta"></p>
    <p class="sub" id="profile"></p>
    <div class="pwarn hide" id="profileWarn"></div>
    <p class="sub"><a class="costhelp" id="costLink" target="_blank">Как читать отчёт — справочник ↗</a></p>
  </header>
  <div class="tabs" id="tabs"></div>
  <div class="controls">
    <div class="group"><label>Ядро</label><div class="seg" id="core"></div></div>
    <div class="group"><label>Группировать</label><div class="seg" id="by"></div></div>
    <div class="group"><label>Поиск</label><input type="search" id="q" placeholder="объект, шейдер, keyword, RT"></div>
    <div class="group project-only"><label>Показать</label><div class="seg" id="pfilter"></div></div>
    <div class="group"><button class="btn" id="all"></button></div>
  </div>
  <div class="stagebar" id="stagebar"></div>
  <div class="stages" id="stages"></div>
  <section id="loops"></section>
  <section id="memory"></section>
  <section id="main"></section>
  <section id="missing"></section>
  <section id="project"></section>`;
  Object.assign(AUTO_TIPS, {
    "повторов цикла, n": "Сколько раз выполняются динамические циклы во всех шейдерах с ↻",
    "Шейдер · пасс": "Шейдер и его проход (pass): у одного шейдера несколько проходов, у каждого своя цена",
    "в кадре": "Шейдеры проекта, которые есть в этом кадре", "не в кадре": "Шейдеры материалов проекта, которых нет в этом кадре",
    "события": "Каждое событие (draw / dispatch) отдельно",
  });
  const TOP = 50;
  const CUM_HINT = "Сумма «% шейдерных циклов» этой строки и всех выше: «первые N строк — это X% циклов кадра». Только при сортировке по итогу.";
  const STAGE_NAMES = {post: "пост", opaque: "opaque", transparent: "transparent", shadow: "тени", prepass: "prepass",
                       ui: "UI", compute: "compute", other: "прочее"};
  const STAGE_COLORS = {post: "#a594f0", opaque: "#7fb2e5", transparent: "#6cc4c4", shadow: "#8b939c", prepass: "#d9b36e",
                        ui: "#e3a1c4", compute: "#b9a3e0", other: "#5f6873"};
  const BY = [["event", "события"], ["shader", "шейдер"], ["variant", "вариант"], ["object", "объект"], ["rt", "RT"]];
  const st = { core: D.main_core, by: "event", q: "", stage: "", all: false, sort: ["total", -1],
               tab: "frame", pf: "all", psort: ["px", -1], msort: ["time", -1], open: new Set() };
  const P = D.project_shaders;
  const cyc = v => v == null ? "—" : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);
  const mc = v => v == null ? "—" : (v / 1e6).toFixed(v >= 1e8 ? 0 : v >= 1e7 ? 1 : 2);
  const pc = v => v == null ? "—" : (100 * v).toFixed(v >= 0.1 ? 1 : 2) + "%";
  const short = c => c.replace("Mali-", "").replace("Immortalis-", "");
  const plural = (n, one, few, many) =>
    `${n} ${n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many}`;
  const objName = s => String(s || "").replace(/^\(RP \d+:\d+\) /, "");

  const f = D.frame, cov = D.coverage;
  document.title = document.getElementById("title").textContent =
    `Стоимость кадра: ${f.project ? f.project.split(/[\\/]/).pop() : ""} ${D.frame_size || ""}`.trim();
  document.getElementById("meta").textContent =
    `Unity ${f.unity} · ${f.graphics_api} в редакторе, quality ${f.quality}, ${f.play_mode ? "Play Mode" : "Edit Mode"} · ` +
    `цены: malioc ${D.malioc || ""}, ${D.api}` + (D.variants_checked === false ? " (варианты не сверены с шейдерами)" : "") + ` · draw с ценой ${cov.draws_priced} из ${cov.draws} · пиксели: ` +
    Object.entries(D.pixel_methods).map(([k, v]) => `${k} ${v}`).join(", ") + ` · ${D.frame_dir || ""}`;

  // the settings the frame is drawn with (core/profile.py): quality, build target, pipeline asset, the frame's scale
  const PROF = D.profile || {recorded: false, warnings: []};
  const size = a => a ? a.join("×") : "?";
  document.getElementById("profile").textContent = !PROF.recorded ? "" :
    `Quality ${PROF.quality} · платформа ${PROF.build_target || "?"}`
    + (PROF.pipeline ? ` · пайплайн ${PROF.pipeline} (${PROF.pipeline_class}${PROF.asset_scale ? ", RenderScale " + PROF.asset_scale : ""})` : "")
    + ` · Game view ${size(PROF.screen)}, кадр ${size(PROF.frame_size)}` + (PROF.frame_scale ? ` (масштаб ${PROF.frame_scale})` : "")
    + (PROF.features && PROF.features.length ? " · features: " + PROF.features.map(x => x.name + (x.active ? "" : " (выкл.)")).join(", ") : "");
  document.getElementById("profile").title = Object.entries(PROF.fields || {}).map(([k, v]) => `${k} = ${v}`).join("\n");
  {
    const w = PROF.warnings || [], el = document.getElementById("profileWarn");
    el.classList.toggle("hide", !w.length);
    el.innerHTML = w.length ? `<ul>${w.map(x => `<li>${esc(msg(x))}</li>`).join("")}</ul>` : "";
  }

  const coreSeg = document.getElementById("core");
  D.cores.forEach(c => { const b = document.createElement("button"); b.dataset.v = c.name;
    b.textContent = short(c.name) + (c.name === D.main_core ? " ★" : ""); b.title = c.arch;
    b.onclick = () => { st.core = c.name; draw(); }; coreSeg.appendChild(b); });
  const bySeg = document.getElementById("by");
  BY.forEach(([k, l]) => { const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
    b.onclick = () => { st.by = k; st.sort = ["total", -1]; draw(); }; bySeg.appendChild(b); });
  document.getElementById("q").oninput = e => { st.q = e.target.value.toLowerCase(); draw(); };
  document.getElementById("all").onclick = () => { st.all = !st.all; draw(); };

  function cost(r) { return r.cost[st.core]; }
  function match(r) {
    if (st.stage && r.stage !== st.stage) return false;
    if (!st.q) return true;
    return [r.object, r.shader, r.pass, (r.keywords || []).join(" "), r.rt, r.variant, r.path].join(" ").toLowerCase().includes(st.q);
  }
  document.getElementById("costLink").href = SITE_REF + "#report";
  const pathNote = p => p === "total" ? " · total" : String(p || "").startsWith("loop") ? " · цикл " + esc(p.slice(5)) : "";
  // a dynamic loop (lights per pixel, ray steps): its price at 0, 1, 2, 4, 8 repeats; the used one is in px_path "loop n=2"
  const loopN = () => String((D.loops || {}).n ?? "");  // the default n: the column of the range that is the price
  const byN = c => c.px_price_by_n ? "цена пикселя при числе повторов цикла: " + Object.entries(c.px_price_by_n)
    .map(([n, v]) => `${n} → ${cyc(v)}${n === loopN(c) ? " (в итоге)" : ""}`).join(" · ") : "";
  // what every outer loop of the shader costs at its number of repeats (core/pricing.loop_parts) and where that
  // number is from (r.loop.loops: a uniform of the event, the event's pixels in the RenderDoc capture, the shader's
  // override or the default n), in code order
  const repeats = n => Number.isInteger(+n) ? plural(+n, "повтор", "повтора", "повторов") : `${(+n).toFixed(2)} повтора в среднем`;
  const loopFrom = s => !s ? "" : s.from === "uniform" ? `: ${s.bound} = ${+Number(s.value).toFixed(3)}`
    : s.from === "frame" ? `, из кадра: ${plural(s.pixels, "пиксель", "пикселя", "пикселей")} в RenderDoc`
    : s.from === "override" ? ", задано для шейдера" : ", по умолчанию";
  // the event still has a loop whose number of repeats the frame does not give: the prices at n = 0..8 mean something
  const guessed = r => !r.loop || !(r.loop.loops || []).length || r.loop.loops.some(x => x.from === "default");
  const loopParts = (r, c) => {
    const parts = c.px_loops || c.cs_loops, src = (r.loop || {}).loops || [];
    if (!parts || !parts.length) return "";
    const price = c.px_loops ? c.px_price : c.cs_price;
    return `из ${cyc(price)} циклов ${c.px_loops ? "пикселя" : "потока"} на циклы шейдера приходится: ` + parts.map((x, i) =>
      `${i + 1}-й (${repeats(x.n)}${loopFrom(src[i])}) — ${cyc(x.cycles)}`).join(" · ");
  };
  const PARTS_TIP = "Циклы шейдера по порядку в коде. Для каждого — сколько раз он повторяется и откуда это взято: "
    + "из параметра шейдера этого события (граница цикла — uniform), из кадра (пиксели события отлажены в RenderDoc, "
    + "среднее по ним), заданное для шейдера число или n по умолчанию. "
    + "Цена цикла — цена с ним минус цена без него; части узкого места не обязаны складываться в цену.";
  const loopTip = c => `В шейдере цикл, число повторов которого заранее неизвестно (например, по источникам света на пиксель). `
    + `Цена посчитана для ${loopN(c) ?? "n"} повторов. ` + (byN(c) ? byN(c)[0].toUpperCase() + byN(c).slice(1) : "");
  function loopsTable() {
    const L = D.loops, el = document.getElementById("loops");
    // nothing to vary when the frame gives the repeats of every loop of every event with pixels
    if (!L || !L.events || L.unknown === 0) { el.innerHTML = ""; return; }
    const by = L.by_n[st.core] || {}, ns = Object.keys(by);
    const names = [...new Set(ns.flatMap(n => Object.keys(by[n].stages)))];
    const cell = (n, k) => `<td class="num ${String(n) === String(L.n) ? "gl" : ""}">${k}</td>`;
    el.innerHTML = `<h2 title="Весь кадр, если в каждом шейдере с ↻ цикл повторится n раз (например, n источников света на пиксель). В итоге кадра сейчас n = ${L.n}. Циклы, у которых число повторов взято из параметров события, не меняются.">`
      + `↻ Кадр при разном числе повторов циклов <small>${L.events} событий с циклами · в итоге n = ${L.n}`
      + (L.bounded ? ` · у ${L.bounded} событий часть циклов с числом повторов из параметров — они не меняются` : "")
      + (Object.keys(L.overrides).length ? " · " + Object.entries(L.overrides).map(([k, v]) => esc(k) + " = " + v).join(", ") : "")
      + (L.unforced.length ? ` · ${L.unforced.length} файлов не удалось — цена по total` : "") + `</small></h2>`
      + `<div class="wrap"><table><thead><tr><th class="l">повторов цикла, n</th>${ns.map(n => `<th class="num">${n}</th>`).join("")}</tr></thead><tbody>`
      + `<tr><td class="l">кадр, млн циклов</td>${ns.map(n => cell(n, mc(by[n].total))).join("")}</tr>`
      + `<tr><td class="l" title="Доля кадра, которую дают события с ↻">доля событий с циклами</td>${ns.map(n => cell(n, pc(by[n].loop_share))).join("")}</tr>`
      + names.map(k => `<tr><td class="l">${esc(STAGE_NAMES[k] || k)}</td>${ns.map(n => cell(n, pc(by[n].stages[k] || 0))).join("")}</tr>`).join("")
      + `</tbody></table></div>`;
  }
  // memory traffic of the passes and the roofline on the core's device (core/memory.py, plan K2.3)
  const MEM = D.memory;
  const mb = v => v == null ? "—" : (v / 1048576).toFixed(v >= 10485760 ? 1 : 2);
  const ms = v => v == null ? "—" : v.toFixed(v >= 10 ? 1 : 2);
  // the pass of every event: its bound is shown in the event's details
  const PASS_OF = new Map();
  for (const p of (MEM || {}).passes || []) for (const i of p.events) if (!PASS_OF.has(i)) PASS_OF.set(i, p);
  const passBound = p => ((p.roofline || {})[st.core] || {}).bound;
  const boundWord = b => b === "memory" ? "память" : b === "alu" ? "шейдеры" : "—";
  function memoryTable() {
    const el = document.getElementById("memory");
    if (!MEM || !MEM.passes.length) { el.innerHTML = ""; return; }
    const dev = (MEM.assumptions.devices || {})[st.core], T = MEM.totals, R = (T.roofline || {})[st.core];
    let h = `<h2>Проходы <small>${MEM.passes.length} · ${dev ? esc(dev.device) : short(st.core) + ": нет данных устройства"}</small></h2>`;
    if (R) {
      const memFrame = R.mem_ms > R.alu_ms;
      const other = MEM.passes.filter(p => passBound(p) === (memFrame ? "alu" : "memory"));
      h += `<p class="verdict"><b>Кадр упирается в ${memFrame ? "память" : "шейдеры"}</b>: шейдеры ${ms(R.alu_ms)} мс, память ${ms(R.mem_ms)} мс.`
        + (other.length ? ` В ${memFrame ? "шейдеры" : "память"} упираются: ${other.map(p => esc(p.name)).join(", ")}`
          + (memFrame ? "." : ` — это ${pc(R.memory_cycles_share)} циклов кадра.`) : "") + `</p>`;
    }
    const tgt = t => `<span class="kw" title="${t.width}×${t.height} ${esc(t.format)}, ${mb(t.bytes)} МБ">`
      + `${esc(String(t.name).replace(/_\d+x\d+_.*$/, ""))}${t.depth ? " (глубина)" : ""} `
      + `${t.cleared ? "clear" : t.load ? "<b>L</b>" : "—"}/${t.store ? "<b>S</b>" : "—"}</span>`;
    const passMs = p => { const r = (p.roofline || {})[st.core]; return r ? Math.max(r.alu_ms, r.mem_ms) : null; };
    const maxMs = Math.max(0, ...MEM.passes.map(p => passMs(p) || 0));
    const val = (p, k) => { const r = (p.roofline || {})[st.core] || {};
      return k === "index" ? p.index : k === "name" ? p.name.toLowerCase() : k === "bound" ? passBound(p) || ""
        : k === "time" ? passMs(p) ?? -1 : k === "alu" ? r.alu_ms ?? -1 : k === "mem" ? r.mem_ms ?? -1
        : k === "rt" ? p.targets.length : p.bytes[k] ?? -1; };
    const [mk, md] = st.msort;
    const rows = [...MEM.passes].sort((x, y) => { const a = val(x, mk), b = val(y, mk); return (a < b ? -1 : a > b ? 1 : 0) * md; });
    const mth = (k, label, cls, tip) => `<th class="${cls} ${mk === k ? "sorted" : ""}" data-mk="${k}" title="${tip}">${label}${mk === k ? (md < 0 ? " ↓" : " ↑") : ""}</th>`;
    h += `<div class="wrap ev mem"><table><colgroup><col style="width:4%"><col style="width:19%"><col style="width:8%"><col style="width:13%">`
      + `<col style="width:8%"><col style="width:8%"><col style="width:22%"><col style="width:6%"><col style="width:6%"><col style="width:6%"></colgroup><thead><tr>`
      + mth("index", "#", "num", "Порядок в кадре") + mth("name", "Проход", "l", "Имя прохода")
      + mth("bound", "Упор", "l", "Что дольше: шейдеры или память") + mth("time", "% времени", "l", "Доля во времени кадра")
      + mth("alu", "Шейдеры, мс", "num", "Циклы / (ядра × частота)") + mth("mem", "Память, мс", "num", "Байты / скорость памяти")
      + mth("rt", "RT", "l", "L — читается из памяти, S — пишется в память, clear — очищается")
      + mth("load", "Чтение", "num", "Чтение RT, МБ") + mth("store", "Запись", "num", "Запись RT, МБ")
      + mth("textures", "Текстуры", "num", "Чтение текстур, МБ") + `</tr></thead><tbody>`;
    for (const p of rows) {
      const r = (p.roofline || {})[st.core], open = st.open.has("m:" + p.index), memb = passBound(p) === "memory";
      const t = passMs(p), share = t != null && R && R.frame_ms ? t / R.frame_ms : null;
      h += `<tr class="r ${open ? "open" : ""}" data-g="m:${p.index}">`
        + `<td class="num st">${p.index + 1}</td><td class="l">${esc(p.name)}</td>`
        + `<td class="l">${r ? (memb ? '<span class="warnc">память</span>' : "шейдеры") : "—"}</td>`
        + `<td class="l">${share == null ? "—" : `<div class="pct"><span>${pc(share)}</span><i style="width:${Math.max(1, 100 * t / (maxMs || 1))}%"></i></div>`}</td>`
        + `<td class="num ${r && !memb ? "big" : ""}">${r ? ms(r.alu_ms) : "—"}</td><td class="num ${memb ? "big" : ""}">${r ? ms(r.mem_ms) : "—"}</td>`
        + `<td class="l tg">${p.targets.map(tgt).join("")}</td>`
        + `<td class="num">${mb(p.bytes.load)}</td><td class="num">${mb(p.bytes.store)}</td><td class="num">${mb(p.bytes.textures)}</td></tr>`;
      if (open) h += `<tr class="det"><td colspan="10"><div class="dl">${plural(p.calls, "вызов", "вызова", "вызовов")} · события ${p.events.map(i => i + 1).join(", ") || "—"}</div>`
        + `<div class="dl">Тяжёлые текстуры: `
        + (p.textures.length ? p.textures.map(t => `<span class="kw" title="${esc(t.format)}">${esc(t.name)} ${t.size.join("×")} — ${mb(t.bytes)} МБ</span>`).join("") : "нет")
        + `</div></td></tr>`;
    }
    const A = MEM.assumptions;
    h += `</tbody></table></div><p class="sub" title="Скорость памяти — пиковая, реальная ниже. Вершинные буферы не учтены.">`
      + (dev ? `${dev.cores} ядер × ${dev.mhz} МГц, память ${dev.gbps} ГБ/с · ` : "")
      + `за кадр ${mb(T.bytes.total)} МБ · RT сжаты ×${A.afbc} (AFBC), текстуры как ASTC 6×6</p>`;
    el.innerHTML = h;
  }
  function stages() {
    const g = (D.groups[st.core] || {}).stage || [];
    document.getElementById("stagebar").innerHTML = g.map(a =>
      `<span title="${esc(STAGE_NAMES[a.key] || a.key)} ${pc(a.share)}" style="width:${100 * a.share}%;background:${STAGE_COLORS[a.key] || STAGE_COLORS.other}"></span>`).join("");
    const t = D.totals[st.core] || {};
    document.getElementById("stages").innerHTML =
      `<div class="stage ${st.stage ? "" : "on"}" data-s=""><div class="n">весь кадр · ${short(st.core)}</div><b>${mc(t.total)}</b> <span class="n">млн циклов</span>` +
      `<div class="n">пиксели ${pc(t.total ? t.fragment / t.total : null)} · вершины ${pc(t.total ? t.vertex / t.total : null)}` +
      (t.compute ? ` · compute ${pc(t.compute / t.total)}` : "") + `</div></div>` +
      g.map(a => `<div class="stage ${st.stage === a.key ? "on" : ""}" data-s="${esc(a.key)}">` +
        `<div class="n"><span style="color:${STAGE_COLORS[a.key] || STAGE_COLORS.other}">■</span> ${esc(STAGE_NAMES[a.key] || a.key)} · ${a.events} соб.</div>` +
        `<b>${pc(a.share)}</b><div class="n">${mc(a.total)} млн · ${num(a.pixels)} пикс · ${num(a.vertices)} верш</div></div>`).join("");
    document.querySelectorAll(".stage").forEach(el => el.onclick = () => { st.stage = el.dataset.s; draw(); });
  }
  function groupRows() {
    const key = r => st.by === "object" ? objName(r.object) : st.by === "rt" ? r.rt : r[st.by];
    const m = new Map();
    for (const r of D.events) {
      const c = cost(r); if (!c || !match(r)) continue;
      const k = key(r) || "—";
      if (!m.has(k)) m.set(k, {key: k, events: 0, pixels: 0, vertices: 0, fragment: 0, vertex: 0, total: 0, share: 0,
                               stages: new Set(), shaders: new Set(), pxP: [], vtxP: [], threads: 0, compute: 0});
      const a = m.get(k); a.events++; a.pixels += r.pixels; a.vertices += r.vertices;
      a.threads += r.threads || 0; a.compute += c.compute || 0;
      a.fragment += c.fragment; a.vertex += c.vertex; a.total += c.total; a.share += c.share;
      a.stages.add(r.stage); a.shaders.add(r.shader);
      // prices of the events that did any work (a draw with 0 pixels, e.g. depth-only shadows, says nothing)
      if (r.kind === "compute") { if (r.threads) a.pxP.push(c.cs_price); }
      else { if (r.pixels) a.pxP.push(c.px_price); if (r.vertices) a.vtxP.push(c.vtx_price); }
    }
    // the group's price: cycles per pixel / per vertex weighted by the work (compute: per thread)
    for (const a of m.values()) {
      a.px_price = a.pixels + a.threads ? (a.fragment + a.compute) / (a.pixels + a.threads) : null;
      a.vtx_price = a.vertices ? a.vertex / a.vertices : null;
    }
    return [...m.values()];
  }
  // tooltips of the column headers (the reference explains them in full)
  const HEAD_TIPS = {
    share: "Доля в шейдерных циклах кадра", events: "Сколько событий (draw / dispatch) в группе",
    pixels: "Сколько раз выполнился фрагментный шейдер (метка рядом — откуда число)",
    price: "Цена одного пикселя: циклы самого загруженного блока ядра на самом длинном пути",
    px_price: "Цена одного пикселя: циклы самого загруженного блока ядра на самом длинном пути",
    total: "Пиксели × цена пикселя + вершины × цена вершины, млн циклов",
    px: "Цена одного пикселя на основном ядре", vtx: "Цена одной вершины (Position + Varying)",
    mats: "Сколько материалов проекта дают этот скомпилированный шейдер", regs: "Рабочие регистры: больше 32 — вдвое меньше потоков",
    frame: "Доля шейдерных циклов кадра, которую дают события с тем же скомпилированным кодом",
  };
  function th(k, label, cls = "", small = "") {
    const [sk, sd] = st.sort;
    return `<th class="${cls} ${sk === k ? "sorted" : ""}" data-k="${k}"${HEAD_TIPS[k] ? ` title="${esc(HEAD_TIPS[k])}"` : ""}>${label}${sk === k ? (sd < 0 ? " ↓" : " ↑") : ""}${small ? `<small>${small}</small>` : ""}</th>`;
  }
  function sortRows(rows, get) {
    const [k, d] = st.sort;
    rows.sort((a, b) => { const x = get(a, k), y = get(b, k); return (x < y ? -1 : x > y ? 1 : 0) * d; });
  }
  const BAD_FLAGS = new Set(["spilling", "regs_gt32"]);
  const TIPS = {
    dynamic_loop: "Цикл с заранее неизвестным числом повторов (свет на пиксель, шаги луча): цена зависит от числа повторов, см. ↻",
    low_fp16: "Мало вычислений в half (fp16). На Mali half вдвое дешевле float — кандидат на перевод в half",
    regs_gt32: "Больше 32 регистров: GPU держит меньше потоков сразу и хуже прячет задержки текстур",
    spilling: "Регистров не хватило, данные уходят в память — заметно дороже. Упростите шейдер",
    sfu_bound: "Упирается в спецфункции (sin, exp, pow, rcp, sqrt…) — замените их приближениями",
    renderdoc: "Точно: сколько раз реально выполнился шейдер (счётчик RenderDoc)",
    fullscreen: "Площадь экрана: полноэкранный проход посчитан без RenderDoc",
    diff: "Оценка: сколько пикселей поменялось в Frame Debugger, обычно занижено",
    mesh: "Число вершин меша из Frame Debugger",
    none: "Не посчитано",
  };
  const tip = k => esc(TIPS[k] || "");
  const flagTag = x => `<span class="flag ${x}" title="${tip(x)}">${x}</span>`;
  const methodTag = m => m ? `<span class="m ${esc(m)}" title="${tip(m)}">${esc(m)}</span>` : "";
  function flagCount(flags) {
    if (!flags.length) return "";
    const bad = flags.some(x => BAD_FLAGS.has(x));
    return ` <span class="fc ${bad ? "bad" : ""}" title="${esc(flags.map(x => x + ": " + (TIPS[x] || "")).join(" · "))}">${bad ? "⚠ " : ""}${flags.length}</span>`;
  }
  function detailRow(r, c, cum) {
    const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
    const range = r.pixels_low != null || r.pixels_high != null ? ` (${num(r.pixels_low)} … ${num(r.pixels_high)})` : "";
    const items = r.kind === "compute"
      ? [item("Потоки", `${num(r.threads)} ${methodTag(r.thread_method)}`),
         item("Цикл / поток", `${cyc(c.cs_price)} · ${boundTag(c.cs_bound)}${pathNote(c.cs_path)}`)]
      : [item("Пиксели", `${num(r.pixels)} ${methodTag(r.pixel_method)}${range}`),
         item("Цикл / пиксель", `${cyc(c.px_price)} · ${boundTag(c.px_bound)}${pathNote(c.px_path)}`),
         item("Вершины", `${num(r.vertices)} ${methodTag(r.vertex_method)}`),
         item("Цикл / вершина", `${cyc(c.vtx_price)} · ${boundTag(c.vtx_bound)}`),
         item("Итог: пиксели / вершины", `${mc(c.fragment)} / ${mc(c.vertex)} млн`)];
    items.push(item("Регистры", `${c.px_regs ?? "—"}${c.vtx_regs != null ? " / " + c.vtx_regs : ""}${c.px_fp16 != null ? ` · fp16 ${c.px_fp16}%` : ""}`));
    if (cum != null) items.push(item(`<span title="${CUM_HINT}">Накоплено</span>`, pc(cum)));
    const tags = [`событие ${r.index + 1}`, STAGE_NAMES[r.stage] || r.stage];
    const ps = PASS_OF.get(r.index);
    if (ps && passBound(ps)) tags.push(`проход ${ps.name}: упор в ${boundWord(passBound(ps))}`);
    if (r.kind === "compute") tags.push(`kernel ${r.kernel}`);
    else {
      tags.push(`RT ${String(r.rt || "").replace(/_\d+x\d+_.*$/, "")} ${r.rt_size[0]}×${r.rt_size[1]}`);
      if (r.meshes.length > 1) tags.push(`батч: ${plural(r.meshes.length, "меш", "меша", "мешей")}`);
    }
    const kws = r.kind === "compute" ? "" : r.keywords.length ? r.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">без keywords</span>';
    return `<tr class="det"><td colspan="6"><div class="dg num">${items.join("")}</div>`
      + (byN(c) && guessed(r) ? `<div class="dl" title="${esc(loopTip(c))}">↻ ${esc(byN(c))}</div>` : "")
      + (loopParts(r, c) ? `<div class="dl" title="${esc(PARTS_TIP)}">↻ ${esc(loopParts(r, c))}</div>` : "")
      + (r.pixel_note ? `<div class="dl note">${esc(r.pixel_note)}</div>` : "")
      + (c.flags.length ? `<div class="dl">${c.flags.map(flagTag).join(" ")}</div>` : "")
      + `<div class="dl">${tags.map(t => `<span class="kw">${esc(t)}</span>`).join("")}${kws}</div></td></tr>`;
  }
  function eventsTable() {
    let rows = D.events.filter(r => cost(r) && match(r));
    const n = rows.length;
    const get = (r, k) => { const c = cost(r);
      return k === "name" ? objName(r.object).toLowerCase() : k === "shader" ? (r.shader + r.pass).toLowerCase()
        : k === "pixels" ? (r.kind === "compute" ? r.threads : r.pixels) : k === "price" ? (r.kind === "compute" ? c.cs_price : c.px_price) ?? -1
        : c[k] ?? -1; };
    sortRows(rows, get);
    const max = Math.max(...rows.map(r => cost(r).share), 0);
    const byTotal = (st.sort[0] === "total" || st.sort[0] === "share") && st.sort[1] < 0;
    let cum = 0;
    const shown = st.all ? rows : rows.slice(0, TOP);
    let h = `<h2>События кадра <small>${shown.length} из ${n} · ${short(st.core)} · клик по строке — подробности</small></h2>`;
    h += `<div class="wrap ev"><table><colgroup><col style="width:15%"><col style="width:24%"><col style="width:29%">`
       + `<col style="width:11%"><col style="width:9%"><col style="width:12%"></colgroup><thead><tr>`
       + th("share", "% шейдерных циклов", "l") + th("name", "Объект", "l") + th("shader", "Шейдер · пасс", "l")
       + th("pixels", "Пиксели") + th("price", "Цикл / пикс") + th("total", "Итог, млн") + `</tr></thead><tbody>`;
    for (const r of shown) {
      const c = cost(r); cum += c.share;
      const open = st.open.has(r.index), compute = r.kind === "compute", names = objName(r.object).split(", ");
      const loop = String(c.px_path || "").startsWith("loop") ? ` <span class="lp" title="${esc(loopTip(c))}">↻</span>` : "";
      const warn = r.pixel_method === "none" || r.pixel_note ? ` <span class="pw" title="${esc(r.pixel_note || "пиксели не посчитаны")}">!</span>` : "";
      h += `<tr class="r ${open ? "open" : ""}" data-i="${r.index}">`
         + `<td class="l"><div class="pct"><span>${pc(c.share)}</span><i style="width:${Math.max(1, 100 * c.share / (max || 1))}%"></i></div></td>`
         + `<td class="l"><span class="dot" style="background:${STAGE_COLORS[r.stage] || STAGE_COLORS.other}" title="${esc(STAGE_NAMES[r.stage] || r.stage)}"></span>`
         + `<span class="sh" title="${esc(objName(r.object))}">${esc(names[0])}</span>${names.length > 1 ? ` <span class="st">+${names.length - 1}</span>` : ""}${flagCount(c.flags)}</td>`
         + `<td class="l" title="${esc(r.shader)} · ${esc(compute ? "kernel " + r.kernel : r.pass)}"><span class="shn">${esc(r.shader)}</span> <span class="pass">· ${esc(compute ? r.kernel : r.pass)}</span></td>`
         + `<td class="num">${num(compute ? r.threads : r.pixels)}${warn}</td>`
         + `<td class="num">${cyc(compute ? c.cs_price : c.px_price)}${loop}</td>`
         + `<td class="num big">${mc(c.total)}</td></tr>`;
      if (open) h += detailRow(r, c, byTotal ? cum : null);
    }
    return h + "</tbody></table></div>";
  }
  function groupLabel(a) {
    if (st.by === "variant") {
      const [sh, pass, kw] = String(a.key).split(" | ");
      return `<span class="shn">${esc(sh)}</span> <span class="pass">· ${esc(pass || "")}${kw ? " · " + esc(kw) : ""}</span>`;
    }
    if (st.by === "rt") return `<span class="shn">${esc(String(a.key).replace(/_\d+x\d+_.*$/, ""))}</span>`;
    return `<span class="shn">${esc(st.by === "object" ? objName(a.key) : a.key)}</span>`;
  }
  function groupDetail(a, cum) {
    const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
    const range = p => { if (p.length < 2) return ""; const lo = Math.min(...p), hi = Math.max(...p);
      return hi - lo > 1e-6 ? ` (${cyc(lo)} … ${cyc(hi)})` : ""; };
    const items = [item("Пиксели", num(a.pixels)), item("Цикл / пиксель, среднее", cyc(a.px_price) + range(a.pxP)),
      item("Вершины", num(a.vertices)), item("Цикл / вершина, среднее", cyc(a.vtx_price) + range(a.vtxP)),
      item("Итог: пиксели / вершины", `${mc(a.fragment)} / ${mc(a.vertex)} млн`)];
    if (a.threads) items.push(item("Compute", `${num(a.threads)} потоков · ${mc(a.compute)} млн`));
    if (cum != null) items.push(item(`<span title="${CUM_HINT}">Накоплено</span>`, pc(cum)));
    const stagesTag = [...a.stages].map(s => `<span class="kw"><span class="dot" style="background:${STAGE_COLORS[s] || STAGE_COLORS.other}"></span>${esc(STAGE_NAMES[s] || s)}</span>`).join("");
    const shaders = st.by !== "shader" && st.by !== "variant" ? [...a.shaders].map(s => `<span class="kw">${esc(s)}</span>`).join("") : "";
    return `<tr class="det"><td colspan="6"><div class="dg num">${items.join("")}</div>`
      + `<div class="dl">${stagesTag}${shaders}</div>`
      + (st.by === "rt" || st.by === "variant" ? `<div class="dl">${esc(a.key)}</div>` : "") + `</td></tr>`;
  }
  function groupTable() {
    const rows = groupRows();
    sortRows(rows, (a, k) => k === "name" ? String(a.key).toLowerCase() : a[k] ?? -1);
    const max = Math.max(...rows.map(a => a.share), 0);
    const shown = st.all ? rows : rows.slice(0, TOP);
    const byTotal = (st.sort[0] === "total" || st.sort[0] === "share") && st.sort[1] < 0;
    const label = BY.find(b => b[0] === st.by)[1];
    let cum = 0;
    let h = `<h2>По ${{shader: "шейдерам", variant: "вариантам", object: "объектам", rt: "render target"}[st.by]} <small>${shown.length} из ${rows.length} · ${short(st.core)} · клик по строке — подробности</small></h2>`;
    h += `<div class="wrap ev"><table><colgroup><col style="width:15%"><col style="width:44%"><col style="width:8%">`
       + `<col style="width:12%"><col style="width:9%"><col style="width:12%"></colgroup><thead><tr>`
       + th("share", "% шейдерных циклов", "l") + th("name", label[0].toUpperCase() + label.slice(1), "l") + th("events", "Событий")
       + th("pixels", "Пиксели") + th("px_price", "Цикл / пикс") + th("total", "Итог, млн") + `</tr></thead><tbody>`;
    for (const a of shown) {
      cum += a.share;
      const open = st.open.has("g:" + st.by + ":" + a.key);
      const dots = [...a.stages].map(s => `<span class="dot" style="background:${STAGE_COLORS[s] || STAGE_COLORS.other}" title="${esc(STAGE_NAMES[s] || s)}"></span>`).join("");
      const sub = st.by === "object" || st.by === "rt" ? ` <span class="st">${esc([...a.shaders].join(", "))}</span>` : "";
      h += `<tr class="r ${open ? "open" : ""}" data-g="${esc("g:" + st.by + ":" + a.key)}">`
         + `<td class="l"><div class="pct"><span>${pc(a.share)}</span><i style="width:${Math.max(1, 100 * a.share / (max || 1))}%"></i></div></td>`
         + `<td class="l" title="${esc(a.key)}"><span class="dots">${dots}</span>${groupLabel(a)}${sub}</td>`
         + `<td class="num">${a.events}</td><td class="num">${num(a.pixels + a.threads)}</td>`
         + `<td class="num">${cyc(a.px_price)}</td><td class="num big">${mc(a.total)}</td></tr>`;
      if (open) h += groupDetail(a, byTotal ? cum : null);
    }
    return h + "</tbody></table></div>";
  }
  const tabs = document.getElementById("tabs");
  [["frame", "Кадр"], ["passes", MEM && MEM.passes.length ? `Проходы · ${MEM.passes.length}` : ""],
   ["project", P ? `Шейдеры проекта · ${P.rows.length}` : ""]].forEach(([k, l]) => { if (!l) return;
    const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
    b.onclick = () => { st.tab = k; draw(); }; tabs.appendChild(b); });
  if (!P && !(MEM && MEM.passes.length)) tabs.classList.add("hide");
  const pfSeg = document.getElementById("pfilter");
  [["all", "все"], ["frame", "в кадре"], ["notframe", "не в кадре"]].forEach(([k, l]) => {
    const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
    b.onclick = () => { st.pf = k; draw(); }; pfSeg.appendChild(b); });

  function projectTable() {
    const main = st.core;
    let rows = P.rows.map((r, i) => Object.assign({_id: i}, r)).filter(r => {
      if (st.pf === "frame" && !r.frame_events) return false;
      if (st.pf === "notframe" && r.frame_events) return false;
      if (!st.q) return true;
      return [r.shader, r.pass, r.keyword_sets.flat().join(" "), r.materials.join(" ")].join(" ").toLowerCase().includes(st.q);
    });
    const n = rows.length;
    const get = (r, k) => { const p = r.prices[main] || {};
      return k === "name" ? (r.shader + r.pass).toLowerCase() : k === "mats" ? r.materials.length
        : k === "frame" ? ((r.in_frame || {})[main] || 0) : k === "px" ? (p.px_price ?? -1) : k === "vtx" ? (p.vtx_price ?? -1)
        : k === "regs" ? (p.regs ?? -1) : k.startsWith("core:") ? ((r.prices[k.slice(5)] || {}).px_price ?? -1) : 0; };
    const [sk, sd] = st.psort;
    rows.sort((a, b) => { const x = get(a, sk), y = get(b, sk); return (x < y ? -1 : x > y ? 1 : 0) * sd; });
    const shown = st.all ? rows : rows.slice(0, TOP);
    const others = D.cores.filter(c => c.name !== main);
    const pth = (k, label, cls = "") => `<th class="${cls} ${sk === k ? "sorted" : ""}" data-pk="${k}"${HEAD_TIPS[k] || k.startsWith("core:") ? ` title="${esc(HEAD_TIPS[k] || "Цена пикселя на " + k.slice(5))}"` : ""}>${label}`
      + `${sk === k ? (sd < 0 ? " ↓" : " ↑") : ""}</th>`;
    const fixed = 9 + 9 + 8 + 9;
    const coreW = others.length ? Math.min(7, 24 / others.length) : 0;
    const nameW = 100 - fixed - 7 - coreW * others.length;
    let h = `<h2>Шейдеры проекта <small>${shown.length} из ${n} · ${P.materials} материалов → ${P.variants} вариантов · ${short(main)} · `
      + `один ряд — один скомпилированный шейдер · клик по строке — подробности</small></h2>`;
    h += `<div class="wrap ev"><table><colgroup><col style="width:${nameW}%"><col style="width:7%"><col style="width:9%"><col style="width:9%">`
       + others.map(() => `<col style="width:${coreW}%">`).join("") + `<col style="width:8%"><col style="width:9%"></colgroup><thead><tr>`
       + pth("name", "Шейдер · пасс", "l") + pth("mats", "Материалы") + pth("px", "Цикл / пикс") + pth("vtx", "Цикл / верш")
       + others.map(c => pth("core:" + c.name, short(c.name))).join("")
       + pth("regs", "Регистры") + pth("frame", "% шейдерных циклов") + `</tr></thead><tbody>`;
    for (const r of shown) {
      const p = r.prices[main] || {}, f = (r.in_frame || {})[main];
      const open = st.open.has("p:" + r._id);
      h += `<tr class="r ${open ? "open" : ""}" data-g="p:${r._id}">`
         + `<td class="l" title="${esc(r.shader)} · ${esc(r.pass)}"><span class="dot" style="background:${r.frame_events ? "var(--accent)" : "var(--line)"}" title="${r.frame_events ? "есть в кадре" : "нет в кадре"}"></span>`
         + `<span class="shn">${esc(r.shader)}</span> <span class="pass">· ${esc(r.pass)}</span>${flagCount(p.flags || [])}</td>`
         + `<td class="num">${r.materials.length}</td>`
         + `<td class="num big">${cyc(p.px_price)}${String(p.px_path || "").startsWith("loop") ? ' <span class="lp">↻</span>' : ""}</td>`
         + `<td class="num">${cyc(p.vtx_price)}</td>`
         + others.map(c => `<td class="num st">${cyc((r.prices[c.name] || {}).px_price)}</td>`).join("")
         + `<td class="num ${p.regs > 32 ? "warnc" : ""}">${p.regs ?? "—"}</td>`
         + `<td class="num">${r.frame_events ? pc(f || 0) : "—"}</td></tr>`;
      if (open) {
        const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
        const kw = r.keyword_sets[0] || [];
        h += `<tr class="det"><td colspan="${6 + others.length}"><div class="dg num">`
           + item("Цикл / пиксель", `${cyc(p.px_price)} · ${boundTag(p.px_bound)}${pathNote(p.px_path)}`)
           + item("Цикл / вершина", `${cyc(p.vtx_price)} · ${boundTag(p.vtx_bound)}`)
           + item("Регистры", `${p.regs ?? "—"}${p.fp16 != null ? ` · fp16 ${p.fp16}%` : ""}`)
           + item("В кадре", r.frame_events ? `${pc(f || 0)} · ${plural(r.frame_events, "событие", "события", "событий")}` : "нет")
           + `</div>`
           + ((p.flags || []).length ? `<div class="dl">${p.flags.map(x => `<span class="flag ${x}">${x}</span>`).join(" ")}</div>` : "")
           + `<div class="dl">${kw.length ? kw.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">без keywords</span>'}`
           + (r.keyword_sets.length > 1 ? ` · ещё ${r.keyword_sets.length - 1} наборов keywords с тем же кодом` : "") + `</div>`
           + `<div class="dl">Материалы: ${r.materials.map(esc).join(", ")}</div></td></tr>`;
      }
    }
    h += "</tbody></table></div>";
    const list = (title, items, line) => items.length ? `<details style="margin-top:12px"><summary class="st">${title} · ${items.length}</summary>`
      + `<div class="wrap"><table><tbody>${items.map(line).join("")}</tbody></table></div></details>` : "";
    h += list("Варианты без цены (не скомпилированы: нужен открытый редактор)", P.unpriced, u =>
      `<tr><td class="l">${esc(u.shader)} · ${esc(u.pass)}</td><td class="l">${u.keywords.map(esc).join(" ")}</td>`
      + `<td class="num">${u.materials.length}</td><td class="l fail">${esc(u.reason)}</td></tr>`);
    h += list("Материалы, которые не попали в таблицу", P.skipped, x =>
      `<tr><td class="l">${esc(x.material)}</td><td class="l">${esc(x.shader || "")}</td><td class="l fail">${esc(x.reason)}</td></tr>`);
    return h;
  }

  function bindOpen() {
    document.querySelectorAll("#main tr.r, #project tr.r, #memory tr.r").forEach(el => el.onclick = () => {
      const k = el.dataset.g ?? +el.dataset.i; st.open.has(k) ? st.open.delete(k) : st.open.add(k); draw(); });
  }
  function draw() {
    tabs.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.tab));
    pfSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.pf));
    const proj = st.tab === "project", passes = st.tab === "passes";
    document.querySelectorAll(".project-only").forEach(el => el.classList.toggle("hide", !proj));
    for (const id of ["stagebar", "stages", "loops", "main", "missing"]) document.getElementById(id).classList.toggle("hide", st.tab !== "frame");
    document.getElementById("memory").classList.toggle("hide", !passes);
    bySeg.closest(".group").classList.toggle("hide", st.tab !== "frame");
    for (const id of ["q", "all"]) document.getElementById(id).closest(".group").classList.toggle("hide", passes);
    document.getElementById("project").classList.toggle("hide", !proj);
    if (passes) {
      coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
      memoryTable();
      document.querySelectorAll("thead th[data-mk]").forEach(el => el.onclick = () => {
        const k = el.dataset.mk; st.msort = [k, st.msort[0] === k ? -st.msort[1] : (k === "name" || k === "index" ? 1 : -1)]; draw(); });
      bindOpen();
      return;
    }
    if (proj) {
      coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
      document.getElementById("all").textContent = st.all ? `Только топ-${TOP}` : "Показать все";
      document.getElementById("project").innerHTML = projectTable();
      document.querySelectorAll("thead th[data-pk]").forEach(el => el.onclick = () => {
        const k = el.dataset.pk; st.psort = [k, st.psort[0] === k ? -st.psort[1] : (k === "name" ? 1 : -1)]; draw(); });
      bindOpen();
      return;
    }
    coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
    bySeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.by));
    document.getElementById("all").textContent = st.all ? `Только топ-${TOP}` : "Показать все";
    stages();
    loopsTable();
    document.getElementById("main").innerHTML = st.by === "event" ? eventsTable() : groupTable();
    document.querySelectorAll("thead th[data-k]").forEach(el => el.onclick = () => {
      const k = el.dataset.k; st.sort = [k, st.sort[0] === k ? -st.sort[1] : (k === "name" || k === "shader" || k === "index" ? 1 : -1)]; draw(); });
    bindOpen();
    document.getElementById("missing").innerHTML = D.missing.length
      ? `<h2>Без цены <small>${D.missing.length} — не входят в итог</small></h2><div class="wrap"><table><tbody>`
        + D.missing.map(m => `<tr><td class="num">${m.index + 1}</td><td class="l">${esc(STAGE_NAMES[m.stage] || m.stage)}</td>`
          + `<td class="l">${esc(objName(m.object))}</td><td class="l">${esc(m.shader)} ${m.pass ? "· " + esc(m.pass) : ""}</td>`
          + `<td class="l fail">${esc(m.reason)}</td></tr>`).join("") + "</tbody></table></div>" : "";
  }
  draw();
}

VIEWS.cost = renderCost;
