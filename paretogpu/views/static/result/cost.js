function renderCost() {
  $("#app").innerHTML = `<header class="costhead">
    ${D.thumb ? `<img class="frthumb" id="frThumb" src="${D.thumb}" alt="Captured frame" title="Замеренный frame (клик — крупнее)">` : ""}
    <div class="costhead-text">
    <h1 id="title"></h1>
    <p class="sub" id="meta"></p>
    <p class="sub" id="profile"></p>
    <div class="pwarn hide" id="profileWarn"></div>
    <p class="sub"><a class="costhelp" id="costLink" target="_blank">Как читать отчёт — справочник ↗</a></p>
    </div>
  </header>
  <div class="tabs" id="tabs"></div>
  <div class="controls">
    <div class="group"><label>Core</label><div class="seg" id="core"></div></div>
    <div class="group"><label>Group by</label><div class="seg" id="by"></div></div>
    <div class="group"><label>Load</label><div class="seg" id="part"></div></div>
    <div class="group"><label>Search</label><input type="search" id="q" placeholder="объект, шейдер, keyword, RT"></div>
    <div class="group project-only"><label>Show</label><div class="seg" id="pfilter"></div></div>
    <div class="group"><button class="btn" id="all"></button></div>
  </div>
  <div class="stagebar" id="stagebar"></div>
  <div class="stages" id="stages"></div>
  <section id="memory"></section>
  <section id="main"></section>
  <section id="geometry"></section>
  <section id="missing"></section>
  <section id="project"></section>`;
  Object.assign(AUTO_TIPS, {
    "Shader · pass": "Шейдер и его pass: у одного шейдера несколько passes, у каждого своя цена",
  });
  const TOP = 50;
  const BY = [["event", "events"], ["shader", "shader"], ["variant", "variant"], ["object", "object"], ["rt", "RT"]];
  const st = { core: D.main_core, part: "total", by: D.frame.device ? "shader" : "event", q: "", stage: "", all: false, sort: ["total", -1],
               tab: "frame", pf: "all", psort: ["px", -1], msort: ["time", -1], open: new Set() };
  const P = D.project_shaders;
  const cyc = v => v == null ? "—" : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);
  const mc = v => v == null ? "—" : (v / 1e6).toFixed(v >= 1e8 ? 0 : v >= 1e7 ? 1 : 2);
  const pc = v => v == null ? "—" : (100 * v).toFixed(v >= 0.1 ? 1 : 2) + "%";
  const short = c => c.replace("Mali-", "").replace("Immortalis-", "");
  const objName = s => String(s || "").replace(/^\(RP \d+:\d+\) /, "");
  const shaderName = r => r.shader || `не опознан: ${objName(r.object)}`;

  const thumb = document.getElementById("frThumb");
  if (thumb) thumb.onclick = () => thumb.classList.toggle("big");
  const f = D.frame, cov = D.coverage;
  document.title = document.getElementById("title").textContent =
    `Стоимость frame: ${f.project ? f.project.split(/[\\/]/).pop() : ""} ${((D.profile || {}).frame_size || []).join("×")}`.trim();
  const dev = f.device || null, PHONE = !!dev;
  document.getElementById("meta").textContent =
    (dev ? `Телефон ${dev.model || "?"} · ${dev.gpu || "GPU ?"} · ${f.graphics_api}`
      + `${dev.captured_at ? " · " + when(dev.captured_at) : ""}${dev.app ? " · " + dev.app : ""} · `
      : `Unity ${f.unity} · ${f.graphics_api} в редакторе, quality ${f.quality}, ${f.play_mode ? "Play Mode" : "Edit Mode"} · `) +
    (dev ? `замер на телефоне · ${D.frame_dir || ""}` :
    `цены: malioc ${D.malioc || ""}` + (D.variants_checked === false ? " (варианты не сверены с шейдерами)" : "") + ` · draw с ценой ${cov.draws_priced} из ${cov.draws} · пиксели: ` +
    Object.entries(D.pixel_methods).map(([k, v]) => `${k} ${v}`).join(", ") + ` · ${D.frame_dir || ""}`);

  const PROF = D.profile || {warnings: []};
  const size = a => a ? a.join("×") : "?";
  const R = PROF.render || {}, screen = `${dev ? "экрана" : "Game view"} ${size(PROF.screen)}`;
  document.getElementById("profile").textContent = `Frame ${size(R.size)}` + (R.scale ? ` (масштаб ${R.scale} от ${screen})` : "")
    + (R.msaa ? ` · MSAA ${R.msaa}×` : "") + (R.color ? ` · ${R.color}` : "")
    + ` · тени ${(R.shadow_maps || []).length ? R.shadow_maps.map(size).join(", ") : "нет"} · passes: ${(R.passes || []).join(", ")}`;
  {
    const w = PROF.warnings || [], el = document.getElementById("profileWarn");
    el.classList.toggle("hide", !w.length);
    el.innerHTML = w.length ? `<ul>${w.map(x => `<li>${esc(msg(x))}</li>`).join("")}</ul>` : "";
  }

  const coreSeg = document.getElementById("core");
  D.cores.forEach(c => { const b = document.createElement("button"); b.dataset.v = c.name;
    b.textContent = short(c.name) + (c.name === D.main_core ? " ★" : "");
    b.onclick = () => { st.core = c.name; draw(); }; coreSeg.appendChild(b); });
  const partSeg = document.getElementById("part");
  [["total", "all"], ["fragment", "pixels"], ["vertex", "vertices"]].forEach(([k, l]) => { const b = document.createElement("button");
    b.dataset.v = k; b.textContent = l; b.onclick = () => { st.part = k; draw(); }; partSeg.appendChild(b);
    if (PHONE && k === "vertex") b.title = "Всё, что draw тратит кроме расчёта цвета: vertex шейдер, tiler, varyings, depth test"; });
  const bySeg = document.getElementById("by");
  BY.forEach(([k, l]) => { const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
    b.onclick = () => { st.by = k; st.sort = ["total", -1]; draw(); }; bySeg.appendChild(b); });
  document.getElementById("q").oninput = e => { st.q = e.target.value.toLowerCase(); draw(); };
  document.getElementById("all").onclick = () => { st.all = !st.all; draw(); };

  function cost(r) {
    const c = r.cost[st.core];
    if (!c || st.part === "total") return c;
    const t = (D.totals[st.core] || {})[st.part] || 0;
    return {...c, total: c[st.part], share: t ? c[st.part] / t : 0};
  }
  const VX = () => st.part === "vertex";
  function match(r) {
    if (st.stage && r.stage !== st.stage) return false;
    if (!st.q) return true;
    return [r.object, r.shader, r.pass, (r.keywords || []).join(" "), r.rt, r.variant, r.path].join(" ").toLowerCase().includes(st.q);
  }
  document.getElementById("costLink").href = SITE_REF + "#report";
  const pathNote = p => p === "total" ? " · total" : String(p || "").startsWith("loop") ? " · цикл " + esc(p.slice(5)) : "";
  const repeats = n => Number.isInteger(+n) ? plural(+n, "повтор", "повтора", "повторов") : `${(+n).toFixed(2)} повтора в среднем`;
  const loopFrom = s => !s ? "" : s.from === "uniform" ? `: ${s.bound} = ${+Number(s.value).toFixed(3)}`
    : s.from === "frame" ? `, из frame: ${plural(s.pixels, "пиксель", "пикселя", "пикселей")} в RenderDoc`
    : ", по умолчанию";
  const loopParts = (r, c) => {
    const parts = c.px_loops || c.cs_loops, src = (r.loop || {}).loops || [];
    if (!parts || !parts.length) return "";
    const price = c.px_loops ? c.px_price : c.cs_price;
    return `из ${cyc(price)} циклов ${c.px_loops ? "пикселя" : "потока"} на циклы шейдера приходится: ` + parts.map((x, i) =>
      `${i + 1}-й (${repeats(x.n)}${loopFrom(src[i])}) — ${cyc(x.cycles)}`).join(" · ");
  };
  const MEM = D.memory;
  const mb = v => v == null ? "—" : (v / 1048576).toFixed(v >= 10485760 ? 1 : 2);
  const ms = v => v == null ? "—" : v.toFixed(v >= 10 ? 1 : 2);
  const PASS_OF = new Map();
  for (const p of (MEM || {}).passes || []) for (const i of p.events) if (!PASS_OF.has(i)) PASS_OF.set(i, p);
  const passBound = p => ((p.roofline || {})[st.core] || {}).bound;
  const boundWord = b => b === "memory" ? "память" : b === "alu" ? "шейдеры" : "—";
  function memoryTable() {
    const el = document.getElementById("memory");
    if (!MEM || !MEM.passes.length) { el.innerHTML = ""; return; }
    const dev = (MEM.assumptions.devices || {})[st.core], T = MEM.totals, R = (T.roofline || {})[st.core];
    let h = `<h2>Passes <small>${MEM.passes.length} · ${dev ? esc(dev.device) : short(st.core) + ": нет данных устройства"}</small></h2>`;
    if (R && !PHONE) {
      const memFrame = R.mem_ms > R.alu_ms;
      const other = MEM.passes.filter(p => passBound(p) === (memFrame ? "alu" : "memory"));
      h += `<p class="verdict"><b>Frame упирается в ${memFrame ? "память" : "шейдеры"}</b>: шейдеры ${ms(R.alu_ms)} мс, память ${ms(R.mem_ms)} мс.`
        + (other.length ? ` В ${memFrame ? "шейдеры" : "память"} упираются: ${[...new Set(other.map(p => p.name))].map(esc).join(", ")}`
          + (memFrame ? "." : ` — это ${pc(R.memory_cycles_share)} циклов frame.`) : "") + `</p>`;
    }
    const tgt = t => `<span class="kw">`
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
    const model = !PHONE, cols = model ? 10 : 7;
    h += `<div class="wrap ev mem"><table><colgroup>` + (model ? `<col style="width:4%"><col style="width:19%"><col style="width:8%"><col style="width:13%">`
      + `<col style="width:8%"><col style="width:8%"><col style="width:22%"><col style="width:6%"><col style="width:6%"><col style="width:6%">`
      : `<col style="width:4%"><col style="width:26%"><col style="width:10%"><col style="width:36%"><col style="width:8%"><col style="width:8%"><col style="width:8%">`) + `</colgroup><thead><tr>`
      + mth("index", "#", "num", "Порядок в frame") + mth("name", "Pass", "l", "Pass name")
      + (model ? mth("bound", "Bottleneck", "l", "Что дольше: шейдеры или память") + mth("time", "% time", "l", "Доля во времени frame")
        + mth("alu", "Shaders, ms", "num", "Циклы / (ядра × частота)") : "") + mth("mem", "Memory, ms", "num", "Байты / скорость памяти")
      + mth("rt", "RT", "l", "L — читается из памяти, S — пишется в память, clear — очищается")
      + mth("load", "Read", "num", "RT read, MB") + mth("store", "Write", "num", "RT write, MB")
      + mth("textures", "Textures", "num", "Texture read, MB") + `</tr></thead><tbody>`;
    for (const p of rows) {
      const r = (p.roofline || {})[st.core], open = st.open.has("m:" + p.index), memb = passBound(p) === "memory";
      const t = passMs(p), share = t != null && R && R.frame_ms ? t / R.frame_ms : null;
      h += `<tr class="r ${open ? "open" : ""}" data-g="m:${p.index}">`
        + `<td class="num st">${p.index + 1}</td><td class="l">${esc(p.name)}</td>`
        + (model ? `<td class="l">${r ? (memb ? '<span class="warnc">память</span>' : "шейдеры") : "—"}</td>`
          + `<td class="l">${share == null ? "—" : `<div class="pct"><span>${pc(share)}</span><i style="width:${Math.max(1, 100 * t / (maxMs || 1))}%"></i></div>`}</td>`
          + `<td class="num ${r && !memb ? "big" : ""}">${r ? ms(r.alu_ms) : "—"}</td>` : "") + `<td class="num ${memb || !model ? "big" : ""}">${r ? ms(r.mem_ms) : "—"}</td>`
        + `<td class="l tg">${p.targets.map(tgt).join("")}</td>`
        + `<td class="num">${mb(p.bytes.load)}</td><td class="num">${mb(p.bytes.store)}</td><td class="num">${mb(p.bytes.textures)}</td></tr>`;
      if (open) h += `<tr class="det"><td colspan="${cols}"><div class="dl">${plural(p.calls, "вызов", "вызова", "вызовов")} · events ${p.events.map(i => i + 1).join(", ") || "—"}</div>`
        + ((p.resolves || []).length ? `<div class="dl">Resolve в тайле: ${p.resolves.map(n => `<span class="kw">${esc(n)}</span>`).join("")}</div>` : "")
        + `<div class="dl">Тяжёлые текстуры: `
        + (p.textures.length ? p.textures.map(t => `<span class="kw">${esc(t.name)} ${t.size.join("×")} — ${mb(t.bytes)} МБ</span>`).join("") : "нет")
        + `</div></td></tr>`;
    }
    const A = MEM.assumptions;
    h += `</tbody></table></div><p class="sub">`
      + (dev ? `${dev.cores} ядер × ${dev.mhz} МГц, память ${dev.gbps} ГБ/с · ` : "")
      + `за frame ${mb(T.bytes.total)} МБ · RT сжаты ×${A.afbc} (AFBC), ${{native: "текстуры в форматах телефона", android: "текстуры в форматах Android из импортёра", astc: "текстуры как ASTC 6×6 (форматы Android не получены)"}[A.textures]}`
      + (model ? ` · циклы passes в сумме равны итогу frame без строк «тайл Mali» (потоки драйвера в тайле, в events)` : "") + `</p>`;
    el.innerHTML = h;
  }
  function geometryTable() {
    const G = D.geometry || [];
    if (!G.length) return "";
    const m = new Map();
    for (const r of G) {
      const k = r.stage + "|" + shaderName(r);
      if (!m.has(k)) m.set(k, {stage: r.stage, shader: shaderName(r), draws: 0, vertices: 0, triangles: 0, cycles: 0, objects: new Set()});
      const a = m.get(k); a.draws++; a.vertices += r.vertices; a.triangles += r.triangles || 0; a.objects.add(objName(r.object));
      a.cycles += (r.cost[D.main_core] || {}).total || 0;
    }
    const rows = [...m.values()].sort((a, b) => b.vertices - a.vertices);
    if (PHONE) return `<h2>Shadows and depth <small>замер телефона · не входят в итог, как и на ПК</small></h2>`
      + `<div class="wrap ev"><table><thead><tr><th class="l">Pass</th><th class="l">Shader</th><th class="num">Draw</th>`
      + `<th class="num">Vertices</th><th class="num">M cycles</th></tr></thead><tbody>`
      + rows.map(a => `<tr><td class="l">${esc(STAGE[a.stage] || a.stage)}</td><td class="l"><span class="shn">${esc(a.shader)}</span></td>`
        + `<td class="num">${a.draws}</td><td class="num">${num(a.vertices)}</td><td class="num">${mc(a.cycles)}</td></tr>`).join("")
      + `</tbody></table></div>`;
    return `<h2>Not priced on PC <small>shadows and depth · ${plural(G.length, "draw", "draw", "draw")}</small></h2>`
      + `<p class="sub">Эти passes на Mali упираются в обработку треугольников (tiler), а не в шейдер: расчёт занижал их в 3–4 раза, `
      + `поэтому их нет в итоге frame и в процентах выше. Настоящая нагрузка — в snapshot с телефона.</p>`
      + `<div class="wrap ev"><table><thead><tr><th class="l">Pass</th><th class="l">Shader</th><th class="num">Draw</th>`
      + `<th class="num">Vertices</th><th class="num">Triangles</th></tr></thead><tbody>`
      + rows.map(a => `<tr><td class="l"><span class="dot" style="background:${stageColor(a.stage)}"></span>`
        + `${esc(STAGE[a.stage] || a.stage)}</td><td class="l"><span class="shn">${esc(a.shader)}</span>`
        + (a.objects.size > 1 ? ` <span class="st">${plural(a.objects.size, "object", "objects", "objects")}</span>` : "")
        + `</td><td class="num">${a.draws}</td><td class="num">${num(a.vertices)}</td><td class="num">${num(a.triangles)}</td></tr>`).join("")
      + `</tbody></table></div>`;
  }
  function stages() {
    const acc = new Map();
    for (const r of D.events) {
      const c = cost(r); if (!c) continue;
      if (!acc.has(r.stage)) acc.set(r.stage, {key: r.stage, events: 0, pixels: 0, vertices: 0, total: 0, share: 0});
      const a = acc.get(r.stage); a.events++; a.pixels += r.pixels; a.vertices += r.vertices; a.total += c.total; a.share += c.share;
    }
    const g = [...acc.values()].sort((a, b) => b.total - a.total);
    document.getElementById("stagebar").innerHTML = g.map(a =>
      `<span style="width:${100 * a.share}%;background:${stageColor(a.key)}"></span>`).join("");
    const t = D.totals[st.core] || {};
    document.getElementById("stages").innerHTML =
      `<div class="stage ${st.stage ? "" : "on"}" data-s=""><div class="n">whole frame · ${short(st.core)}</div><b>${mc(t[st.part])}</b> <span class="n">M cycles</span>` +
      `<div class="n">pixels ${pc(t.total ? t.fragment / t.total : null)} · vertices ${pc(t.total ? t.vertex / t.total : null)}` +
      (t.compute ? ` · compute ${pc(t.compute / t.total)}` : "") + `</div></div>` +
      g.map(a => `<div class="stage ${st.stage === a.key ? "on" : ""}" data-s="${esc(a.key)}">` +
        `<div class="n"><span style="color:${stageColor(a.key)}">■</span> ${esc(STAGE[a.key] || a.key)} · ${plural(a.events, "event", "events", "events")}</div>` +
        `<b>${pc(a.share)}</b><div class="n">${mc(a.total)} M · ${num(a.pixels)} px · ${num(a.vertices)} vert</div></div>`).join("");
    document.querySelectorAll(".stage").forEach(el => el.onclick = () => { st.stage = el.dataset.s; draw(); });
  }
  function groupRows() {
    const key = r => st.by === "object" ? objName(r.object) : st.by === "rt" ? r.rt
      : st.by === "shader" ? shaderName(r) : r[st.by];
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
      // a zero-work draw (e.g. depth-only shadow) has no meaningful price
      if (r.kind === "compute") { if (r.threads) a.pxP.push(c.cs_price); }
      else { if (r.pixels) a.pxP.push(c.px_price); if (r.vertices) a.vtxP.push(c.vtx_price); }
    }
    for (const a of m.values()) {
      a.px_price = a.pixels + a.threads ? (a.fragment + a.compute) / (a.pixels + a.threads) : null;
      a.vtx_price = a.vertices ? a.vertex / a.vertices : null;
    }
    return [...m.values()];
  }
  const HEAD_TIPS = {
    share: "Доля в шейдерных циклах frame", events: "Сколько events (draw / dispatch) в группе",
    pixels: "Потоки фрагментного шейдера (метка рядом — откуда число)",
    price: "Цена одного пикселя: сумма циклов units ядра на самом длинном пути",
    px_price: "Цена одного пикселя: сумма циклов units ядра на самом длинном пути",
    total: "Пиксели × цена пикселя + vertices × цена vertex, млн циклов",
    px: "Цена одного пикселя на основном ядре", vtx: "Цена одного vertex (Position + Varying)",
    mats: "Сколько материалов проекта дают этот скомпилированный шейдер", regs: "Рабочие регистры: больше 32 — вдвое меньше потоков",
    frame: "Доля шейдерных циклов frame, которую дают events с тем же скомпилированным кодом",
  };
  if (PHONE) Object.assign(HEAD_TIPS, {
    pixels: "Сколько пикселей закрасил шейдер за frame, с overdraw (Samples Passed на телефоне)",
    price: "Циклы шейдера на один пиксель, замер на телефоне", px_price: "Циклы шейдера на один пиксель, замер на телефоне",
    total: "Пиксели: циклы пиксельного шейдера (replay frame с выключенным шейдером, разница по самому загруженному unit) "
      + "делятся по пикселям его draw. Vertex: циклы draw с выключенным vertex шейдером минус их пиксельная часть, по vertices. Итог = пиксели + vertex, млн",
  });
  if (PHONE) Object.assign(AUTO_TIPS, {
    "Cycles / pixel, avg": "Циклы шейдера (замер Mali) / его пиксели (Samples Passed)",
    "Cycles / vertex, avg": "Vertex циклы (замер Mali) / vertices draw",
  });
  function th(k, label, cls = "", small = "") {
    const [sk, sd] = st.sort;
    return `<th class="${cls} ${sk === k ? "sorted" : ""}" data-k="${k}"${HEAD_TIPS[k] ? ` title="${esc(HEAD_TIPS[k])}"` : ""}>${label}${sk === k ? (sd < 0 ? " ↓" : " ↑") : ""}${small ? `<small>${small}</small>` : ""}</th>`;
  }
  function sortRows(rows, get) {
    const [k, d] = st.sort;
    rows.sort((a, b) => { const x = get(a, k), y = get(b, k); return (x < y ? -1 : x > y ? 1 : 0) * d; });
  }
  const BAD_FLAGS = new Set(["spilling", "regs_gt32"]);
  const flagTag = x => `<span class="flag ${x}">${x}</span>`;
  function flagCount(flags) {
    if (!flags.length) return "";
    const bad = flags.some(x => BAD_FLAGS.has(x));
    return ` <span class="fc ${bad ? "bad" : ""}">${bad ? "⚠ " : ""}${flags.length}</span>`;
  }
  const texFlags = r => [...new Set((r.textures || []).flatMap(t => t.flags))];
  const SOURCE = {editor: " (формат редактора)"};
  function texturesBlock(r) {
    if (!(r.textures || []).length) return "";
    const line = t => `<div><span class="kw">${t.stage === "frag" ? "PS" : t.stage === "vert" ? "VS" : "CS"}</span> `
      + `${esc(t.name || "—")} · ${t.size.join("×")}${t.cube ? " cube" : ""} · ${esc(t.format)}${SOURCE[t.source] || ""}`
      + ` · ${plural(t.mips || 1, "мип", "мипа", "мипов")} · ${esc(t.filter || "выборка без фильтра")}`
      + `${t.target ? ' <span class="st">RT</span>' : ""}${t.flags.map(f => ` <span class="flag ${f}">${esc(FLAG[f])}</span>`).join("")}</div>`;
    return `<div class="dl tex">${r.textures.map(line).join("")}</div>`;
  }
  const WHY = {noise: "ниже шума", not_swapped: "не подменён", no_output: "нет вывода"};
  const unmeasured = c => Object.entries(c.unmeasured || {}).map(([p, w]) => `${PART[p]} — ${WHY[w] || w}`).join(", ");
  function detailRow(r, c) {
    const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
    const items = r.kind === "compute"
      ? [item("Threads", `${num(r.threads)} ${methodTag(r.thread_method)}`),
         item("Cycles / thread", `${cyc(c.cs_price)} · ${boundTag(c.cs_bound)}${pathNote(c.cs_path)}`)]
      : [item("Pixels", `${num(r.pixels)} ${methodTag(r.pixel_method)}`),
         item("Cycles / pixel", `${cyc(c.px_price)} · ${boundTag(c.px_bound)}${pathNote(c.px_path)}`),
         item("Vertices", `${num(r.vertices)} ${methodTag(r.vertex_method)}`),
         item("Cycles / vertex", `${cyc(c.vtx_price)} · ${boundTag(c.vtx_bound)}`),
         item("Total: pixels / vertices", `${mc(c.fragment)} / ${mc(c.vertex)} M`)];
    if (!PHONE) items.push(item("Registers", `${c.px_regs ?? "—"}${c.vtx_regs != null ? " / " + c.vtx_regs : ""}${c.px_fp16 != null ? ` · fp16 ${c.px_fp16}%` : ""}`));
    const tags = [`event ${r.index + 1}`, STAGE[r.stage] || r.stage];
    const ps = PASS_OF.get(r.index);
    if (ps && passBound(ps)) tags.push(`pass ${ps.name}: bottleneck в ${boundWord(passBound(ps))}`);
    if (r.kind === "compute") tags.push(`kernel ${r.kernel}`);
    else {
      tags.push(`RT ${String(r.rt || "").replace(/_\d+x\d+_.*$/, "")} ${r.rt_size[0]}×${r.rt_size[1]}`);
      if (r.meshes.length > 1) tags.push(`батч: ${plural(r.meshes.length, "меш", "меша", "мешей")}`);
    }
    const kws = r.kind === "compute" ? "" : r.keywords.length ? r.keywords.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">no keywords</span>';
    return `<tr class="det"><td colspan="6"><div class="dg num">${items.join("")}</div>`
      + (loopParts(r, c) ? `<div class="dl">↻ ${esc(loopParts(r, c))}</div>` : "")
      + (r.pixel_note ? `<div class="dl note">${esc(r.pixel_note)}</div>` : "")
      + (unmeasured(c) ? `<div class="dl note">цена модели, не замер: ${esc(unmeasured(c))}</div>` : "")
      + (c.flags.length ? `<div class="dl">${c.flags.map(flagTag).join(" ")}</div>` : "")
      + texturesBlock(r)
      + `<div class="dl">${tags.map(t => `<span class="kw">${esc(t)}</span>`).join("")}${kws}</div></td></tr>`;
  }
  function eventsTable() {
    let rows = D.events.filter(r => cost(r) && match(r));
    const n = rows.length;
    const get = (r, k) => { const c = cost(r);
      return k === "name" ? objName(r.object).toLowerCase() : k === "shader" ? (r.shader + r.pass).toLowerCase()
        : k === "pixels" ? (VX() ? r.vertices : r.kind === "compute" ? r.threads : r.pixels)
        : k === "price" ? (VX() ? c.vtx_price : r.kind === "compute" ? c.cs_price : c.px_price) ?? -1
        : c[k] ?? -1; };
    sortRows(rows, get);
    const max = Math.max(...rows.map(r => cost(r).share), 0);
    const shown = st.all ? rows : rows.slice(0, TOP);
    let h = `<h2>Frame events <small>${shown.length} из ${n} · ${short(st.core)} · клик по строке — подробности</small></h2>`;
    h += `<div class="wrap ev"><table><colgroup><col style="width:15%"><col style="width:24%"><col style="width:29%">`
       + `<col style="width:11%"><col style="width:9%"><col style="width:12%"></colgroup><thead><tr>`
       + th("share", "% shader cycles", "l") + th("name", "Object", "l") + th("shader", "Shader · pass", "l")
       + th("pixels", VX() ? "Vertices" : "Pixels") + th("price", VX() ? "Cycles / vert" : "Cycles / px") + th("total", "Total, M") + `</tr></thead><tbody>`;
    for (const r of shown) {
      const c = cost(r);
      const open = st.open.has(r.index), compute = r.kind === "compute", names = objName(r.object).split(", ");
      const loop = String(c.px_path || "").startsWith("loop") ? ` <span class="lp">↻</span>` : "";
      const warn = r.pixel_method === "none" || r.pixel_note || unmeasured(c) ? ` <span class="pw">!</span>` : "";
      h += `<tr class="r ${open ? "open" : ""}" data-i="${r.index}">`
         + `<td class="l"><div class="pct"><span>${pc(c.share)}</span><i style="width:${Math.max(1, 100 * c.share / (max || 1))}%"></i></div></td>`
         + `<td class="l"><span class="dot" style="background:${stageColor(r.stage)}"></span>`
         + `<span class="sh">${esc(names[0])}</span>${names.length > 1 ? ` <span class="st">+${names.length - 1}</span>` : ""}${flagCount([...c.flags, ...texFlags(r)])}</td>`
         + `<td class="l"><span class="shn">${esc(shaderName(r))}</span> <span class="pass">· ${esc(compute ? r.kernel : r.pass)}</span></td>`
         + `<td class="num">${VX() ? num(r.vertices) : num(compute ? r.threads : r.pixels) + warn}</td>`
         + `<td class="num">${VX() ? cyc(c.vtx_price) : cyc(compute ? c.cs_price : c.px_price) + loop}</td>`
         + `<td class="num big">${mc(c.total)}</td></tr>`;
      if (open) h += detailRow(r, c);
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
  function groupDetail(a) {
    const item = (label, value) => `<div><span>${label}</span>${value}</div>`;
    const range = p => { if (p.length < 2) return ""; const lo = Math.min(...p), hi = Math.max(...p);
      return hi - lo > 1e-6 ? ` (${cyc(lo)} … ${cyc(hi)})` : ""; };
    const items = [item("Pixels", num(a.pixels)), item("Cycles / pixel, avg", cyc(a.px_price) + range(a.pxP)),
      item("Vertices", num(a.vertices)), item("Cycles / vertex, avg", cyc(a.vtx_price) + range(a.vtxP)),
      item("Total: pixels / vertices", `${mc(a.fragment)} / ${mc(a.vertex)} M`)];
    if (a.threads) items.push(item("Compute", `${num(a.threads)} потоков · ${mc(a.compute)} млн`));
    const stagesTag = [...a.stages].map(s => `<span class="kw"><span class="dot" style="background:${stageColor(s)}"></span>${esc(STAGE[s] || s)}</span>`).join("");
    const shaders = st.by !== "shader" && st.by !== "variant" ? [...a.shaders].map(s => `<span class="kw">${esc(s)}</span>`).join("") : "";
    return `<tr class="det"><td colspan="6"><div class="dg num">${items.join("")}</div>`
      + `<div class="dl">${stagesTag}${shaders}</div>`
      + (st.by === "rt" || st.by === "variant" ? `<div class="dl">${esc(a.key)}</div>` : "") + `</td></tr>`;
  }
  function groupTable() {
    const rows = groupRows();
    sortRows(rows, (a, k) => k === "name" ? String(a.key).toLowerCase() : k === "pixels" ? (VX() ? a.vertices : a.pixels + a.threads)
      : k === "px_price" && VX() ? a.vtx_price ?? -1 : a[k] ?? -1);
    const max = Math.max(...rows.map(a => a.share), 0);
    const shown = st.all ? rows : rows.slice(0, TOP);
    const label = BY.find(b => b[0] === st.by)[1];
    let h = `<h2>By ${{shader: "shader", variant: "variant", object: "object", rt: "render target"}[st.by]} <small>${shown.length} из ${rows.length} · ${short(st.core)} · клик по строке — подробности</small></h2>`;
    h += `<div class="wrap ev"><table><colgroup><col style="width:15%"><col style="width:44%"><col style="width:8%">`
       + `<col style="width:12%"><col style="width:9%"><col style="width:12%"></colgroup><thead><tr>`
       + th("share", "% shader cycles", "l") + th("name", label[0].toUpperCase() + label.slice(1), "l") + th("events", "Events")
       + th("pixels", VX() ? "Vertices" : "Pixels") + th("px_price", VX() ? "Cycles / vert" : "Cycles / px") + th("total", "Total, M") + `</tr></thead><tbody>`;
    for (const a of shown) {
      const open = st.open.has("g:" + st.by + ":" + a.key);
      const dots = [...a.stages].map(s => `<span class="dot" style="background:${stageColor(s)}"></span>`).join("");
      const sub = st.by === "object" || st.by === "rt" ? ` <span class="st">${esc([...a.shaders].join(", "))}</span>` : "";
      h += `<tr class="r ${open ? "open" : ""}" data-g="${esc("g:" + st.by + ":" + a.key)}">`
         + `<td class="l"><div class="pct"><span>${pc(a.share)}</span><i style="width:${Math.max(1, 100 * a.share / (max || 1))}%"></i></div></td>`
         + `<td class="l"><span class="dots">${dots}</span>${groupLabel(a)}${sub}</td>`
         + `<td class="num">${a.events}</td><td class="num">${num(VX() ? a.vertices : a.pixels + a.threads)}</td>`
         + `<td class="num">${cyc(VX() ? a.vtx_price : a.px_price)}</td><td class="num big">${mc(a.total)}</td></tr>`;
      if (open) h += groupDetail(a);
    }
    return h + "</tbody></table></div>";
  }
  const tabs = document.getElementById("tabs");
  [["frame", "Frame"], ["passes", !dev && MEM && MEM.passes.length ? `Passes · ${MEM.passes.length}` : ""],
   ["project", P ? `Project shaders · ${P.rows.length}` : ""]].forEach(([k, l]) => { if (!l) return;
    const b = document.createElement("button"); b.dataset.v = k; b.textContent = l;
    b.onclick = () => { st.tab = k; draw(); }; tabs.appendChild(b); });
  if (!P && (dev || !(MEM && MEM.passes.length))) tabs.classList.add("hide");
  const pfSeg = document.getElementById("pfilter");
  [["all", "all"], ["frame", "in frame"], ["notframe", "not in frame"]].forEach(([k, l]) => {
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
    let h = `<h2>Project shaders <small>${shown.length} из ${n} · ${plural(P.materials, "материал", "материала", "материалов")} → ${plural(P.variants, "variant", "variants", "variants")} · ${short(main)} · `
      + `один ряд — один скомпилированный шейдер · клик по строке — подробности</small></h2>`;
    h += `<div class="wrap ev"><table><colgroup><col style="width:${nameW}%"><col style="width:7%"><col style="width:9%"><col style="width:9%">`
       + others.map(() => `<col style="width:${coreW}%">`).join("") + `<col style="width:8%"><col style="width:9%"></colgroup><thead><tr>`
       + pth("name", "Shader · pass", "l") + pth("mats", "Materials") + pth("px", "Cycles / px") + pth("vtx", "Cycles / vert")
       + others.map(c => pth("core:" + c.name, short(c.name))).join("")
       + pth("regs", "Registers") + pth("frame", "% shader cycles") + `</tr></thead><tbody>`;
    for (const r of shown) {
      const p = r.prices[main] || {}, f = (r.in_frame || {})[main];
      const open = st.open.has("p:" + r._id);
      h += `<tr class="r ${open ? "open" : ""}" data-g="p:${r._id}">`
         + `<td class="l"><span class="dot" style="background:${r.frame_events ? "var(--accent)" : "var(--line)"}"></span>`
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
           + item("Cycles / pixel", `${cyc(p.px_price)} · ${boundTag(p.px_bound)}${pathNote(p.px_path)}`)
           + item("Cycles / vertex", `${cyc(p.vtx_price)} · ${boundTag(p.vtx_bound)}`)
           + item("Registers", `${p.regs ?? "—"}${p.fp16 != null ? ` · fp16 ${p.fp16}%` : ""}`)
           + item("В frame", r.frame_events ? `${pc(f || 0)} · ${plural(r.frame_events, "event", "events", "events")}` : "нет")
           + `</div>`
           + ((p.flags || []).length ? `<div class="dl">${p.flags.map(x => `<span class="flag ${x}">${x}</span>`).join(" ")}</div>` : "")
           + `<div class="dl">${kw.length ? kw.map(k => `<span class="kw">${esc(k)}</span>`).join("") : '<span class="kw">no keywords</span>'}`
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
    for (const id of ["stagebar", "stages", "main", "geometry", "missing"]) document.getElementById(id).classList.toggle("hide", st.tab !== "frame");
    document.getElementById("memory").classList.toggle("hide", !passes);
    bySeg.closest(".group").classList.toggle("hide", st.tab !== "frame");
    partSeg.closest(".group").classList.toggle("hide", st.tab !== "frame");
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
      document.getElementById("all").textContent = st.all ? `Top ${TOP} only` : "Show all";
      document.getElementById("project").innerHTML = projectTable();
      document.querySelectorAll("thead th[data-pk]").forEach(el => el.onclick = () => {
        const k = el.dataset.pk; st.psort = [k, st.psort[0] === k ? -st.psort[1] : (k === "name" ? 1 : -1)]; draw(); });
      bindOpen();
      return;
    }
    coreSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.core));
    bySeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.by));
    partSeg.querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === st.part));
    document.getElementById("all").textContent = st.all ? `Top ${TOP} only` : "Show all";
    stages();
    document.getElementById("main").innerHTML = st.by === "event" ? eventsTable() : groupTable();
    document.querySelectorAll("thead th[data-k]").forEach(el => el.onclick = () => {
      const k = el.dataset.k; st.sort = [k, st.sort[0] === k ? -st.sort[1] : (k === "name" || k === "shader" || k === "index" ? 1 : -1)]; draw(); });
    bindOpen();
    document.getElementById("geometry").innerHTML = geometryTable();
    document.getElementById("missing").innerHTML = D.missing.length
      ? `<h2>No price <small>${D.missing.length} — не входят в итог</small></h2><div class="wrap"><table><tbody>`
        + D.missing.map(m => `<tr><td class="num">${m.index + 1}</td><td class="l">${esc(STAGE[m.stage] || m.stage)}</td>`
          + `<td class="l">${esc(objName(m.object))}</td><td class="l">${esc(m.shader)} ${m.pass ? "· " + esc(m.pass) : ""}</td>`
          + `<td class="l fail">${esc(m.reason)}</td></tr>`).join("") + "</tbody></table></div>" : "";
  }
  draw();
}

VIEWS.cost = renderCost;
