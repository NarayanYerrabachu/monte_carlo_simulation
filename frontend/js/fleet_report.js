// Fleet report page: fetches /v1/reports/fleet and renders it; PDF and Excel come from the same run.

// ── formatting (German) ─────────────────────────────────────────────────────
const nf = (d = 0) => new Intl.NumberFormat("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
const num = (v, d = 0) => nf(d).format(v);
const pct = (v, d = 1) => `${nf(d).format(v * 100)}%`;
const eur = (v) => `€${nf(0).format(v)}`;

// ── small DOM helpers ───────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function html(tag, cls, text, parent) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || 1;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// tooltip shared per card
function tooltip(container) {
  const card = container.closest(".chart-card");
  let tip = card.querySelector(".tip");
  if (!tip) { tip = html("div", "tip", "", card); tip.hidden = true; }
  return {
    show(evtOrPoint, lines) {
      const r = card.getBoundingClientRect();
      const x = evtOrPoint.clientX - r.left, y = evtOrPoint.clientY - r.top;
      tip.replaceChildren();
      lines.forEach((l, i) => { const s = html(i === 0 ? "b" : "div", "", l, tip); if (i === 0) tip.appendChild(document.createElement("br")); });
      tip.style.left = `${Math.min(Math.max(x, 90), r.width - 90)}px`;
      tip.style.top = `${y}px`;
      tip.hidden = false;
    },
    hide() { tip.hidden = true; },
  };
}

// ── histogram with reference lines ─────────────────────────────────────────
function histogram(id, h, opt) {
  const box = $(id);
  box.replaceChildren();
  const W = 420, H = 230, m = { l: 44, r: 12, t: 36, b: 42 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opt.aria }, box);
  const total = h.counts.reduce((a, b) => a + b, 0);
  const share = h.counts.map((c) => c / total);
  const x0 = h.edges[0], x1 = h.edges[h.edges.length - 1];
  const yMax = Math.max(...share) * 1.12;
  const X = (v) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - (v / yMax) * (H - m.t - m.b);
  const tip = tooltip(box);

  for (const t of niceTicks(0, yMax, 4)) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), stroke: "var(--grid)" }, svg);
    el("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end" }, svg).textContent = `${num(t * 100, t * 100 < 10 && t > 0 ? 1 : 0)}%`;
  }
  for (const t of niceTicks(x0, x1, 5)) {
    el("text", { x: X(t), y: H - m.b + 18, "text-anchor": "middle" }, svg).textContent = opt.fmtX(t);
  }
  el("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: "var(--line)" }, svg);
  el("text", { x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle", class: "axis-title" }, svg).textContent = opt.xLabel;
  el("text", { x: m.l - 38, y: m.t - 14, class: "axis-title" }, svg).textContent = "Share of days";

  share.forEach((s, i) => {
    const a = X(h.edges[i]) + 1, b = X(h.edges[i + 1]) - 1;
    const y = Y(s), w = Math.max(1, b - a);
    const bar = el("rect", { x: a, y, width: w, height: Math.max(0, H - m.b - y), rx: Math.min(2, w / 2), fill: opt.color || "var(--s1)" }, svg);
    const hit = el("rect", { x: a - 1, y: m.t, width: w + 2, height: H - m.t - m.b, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (e) => {
      bar.setAttribute("opacity", ".75");
      tip.show(e, [opt.fmtRange(h.edges[i], h.edges[i + 1]), `${pct(s)} of days`]);
    });
    hit.addEventListener("mouseleave", () => { bar.removeAttribute("opacity"); tip.hide(); });
  });

  // reference lines, labels staggered so they never collide
  (opt.markers || []).forEach((mk, i) => {
    const x = X(mk.x);
    if (x < m.l || x > W - m.r) return;
    el("line", { x1: x, x2: x, y1: m.t - 4, y2: H - m.b, stroke: "var(--ink)", "stroke-width": 1.5, "stroke-dasharray": mk.dash ? "4 3" : "" }, svg);
    const anchor = x > W * 0.75 ? "end" : x < W * 0.25 ? "start" : "middle";
    el("text", { x: x + (anchor === "end" ? -4 : anchor === "start" ? 4 : 0), y: m.t - 8 - (i % 2) * 14, "text-anchor": anchor, class: "mark-label" }, svg).textContent = mk.label;
  });
}

// ── horizontal diverging bars ──────────────────────────────────────────────
function diverging(id, items, aria) {
  const box = $(id);
  box.replaceChildren();
  const rowH = 28, W = 440, m = { l: 150, r: 40, t: 8, b: 26 };
  const H = m.t + m.b + rowH * items.length;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": aria }, box);
  const X = (v) => m.l + ((v + 1) / 2) * (W - m.l - m.r);
  const tip = tooltip(box);
  for (const t of [-1, -0.5, 0, 0.5, 1]) {
    el("line", { x1: X(t), x2: X(t), y1: m.t, y2: H - m.b, stroke: t === 0 ? "var(--line)" : "var(--grid)" }, svg);
    el("text", { x: X(t), y: H - 8, "text-anchor": "middle" }, svg).textContent = num(t, 1);
  }
  items.forEach((it, i) => {
    const y = m.t + i * rowH + 6, h = rowH - 12;
    el("text", { x: m.l - 10, y: y + h / 2 + 4, "text-anchor": "end", style: "fill:var(--ink-2)" }, svg).textContent = it.name;
    const a = X(Math.min(0, it.value)), b = X(Math.max(0, it.value));
    const bar = el("rect", { x: a, y, width: Math.max(1.5, b - a), height: h, rx: 3, fill: it.value >= 0 ? "var(--s2)" : "var(--s1)" }, svg);
    el("text", { x: it.value >= 0 ? b + 6 : a - 6, y: y + h / 2 + 4, "text-anchor": it.value >= 0 ? "start" : "end", class: "mark-label" }, svg).textContent = num(it.value, 2);
    const hit = el("rect", { x: m.l, y: y - 4, width: W - m.l - m.r, height: rowH, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (e) => { bar.setAttribute("opacity", ".75"); tip.show(e, [it.name, `ρ = ${num(it.value, 3)}`]); });
    hit.addEventListener("mouseleave", () => { bar.removeAttribute("opacity"); tip.hide(); });
  });
}

// ── line chart with optional band and crosshair ────────────────────────────
function lineChart(id, opt) {
  const box = $(id);
  box.replaceChildren();
  const W = 820, H = 280, m = { l: 52, r: 20, t: 20, b: 44 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opt.aria }, box);
  const xs = opt.x;
  const tx = opt.log ? Math.log10 : (v) => v;
  const xa = tx(xs[0]), xb = tx(xs[xs.length - 1]);
  const X = (v) => m.l + ((tx(v) - xa) / (xb - xa)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - ((v - opt.y0) / (opt.y1 - opt.y0)) * (H - m.t - m.b);
  const tip = tooltip(box);

  for (const t of niceTicks(opt.y0, opt.y1, 5)) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), stroke: "var(--grid)" }, svg);
    el("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end" }, svg).textContent = pct(t, 0);
  }
  const xt = opt.log ? [50, 100, 200, 500, 1000, 2000, 5000, 10000] : xs.filter((_, i) => i % 2 === 0);
  for (const t of xt) el("text", { x: X(t), y: H - m.b + 20, "text-anchor": "middle" }, svg).textContent = num(t);
  el("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: "var(--line)" }, svg);
  el("text", { x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle", class: "axis-title" }, svg).textContent = opt.xLabel;

  (opt.refs || []).forEach((r) => {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(r.y), y2: Y(r.y), stroke: "var(--ink-2)", "stroke-width": 1.2, "stroke-dasharray": "5 4" }, svg);
    el("text", { x: m.l + 6, y: Y(r.y) - 6, class: "mark-label" }, svg).textContent = r.label;
  });
  if (opt.band) {
    const up = xs.map((x, i) => `${X(x)},${Y(opt.band.hi[i])}`);
    const dn = xs.map((x, i) => `${X(x)},${Y(opt.band.lo[i])}`).reverse();
    el("polygon", { points: [...up, ...dn].join(" "), fill: "color-mix(in srgb, var(--s1) 22%, transparent)" }, svg);
  }
  opt.series.forEach((s) => {
    el("polyline", { points: xs.map((x, i) => `${X(x)},${Y(s.y[i])}`).join(" "), fill: "none", stroke: s.color, "stroke-width": 2.2, "stroke-linejoin": "round" }, svg);
    if (opt.dots) xs.forEach((x, i) => el("circle", { cx: X(x), cy: Y(s.y[i]), r: 4, fill: s.color, stroke: "var(--panel)", "stroke-width": 2 }, svg));
    const li = xs.length - 1;
    if (s.endLabel) el("text", { x: X(xs[li]) - 4, y: Y(s.y[li]) + (s.labelDy || -10), "text-anchor": "end", class: "mark-label" }, svg).textContent = s.endLabel;
  });

  const cross = el("line", { y1: m.t, y2: H - m.b, stroke: "var(--muted)", "stroke-width": 1, visibility: "hidden" }, svg);
  const hit = el("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (e) => {
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    let best = 0;
    xs.forEach((x, i) => { if (Math.abs(X(x) - p.x) < Math.abs(X(xs[best]) - p.x)) best = i; });
    cross.setAttribute("x1", X(xs[best])); cross.setAttribute("x2", X(xs[best])); cross.setAttribute("visibility", "visible");
    tip.show(e, opt.tipLines(best));
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hide(); });
}

// ── stacked 100 % bar with labels ──────────────────────────────────────────
function stack(barId, labelsId, parts) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  const bar = $(barId), labels = $(labelsId);
  bar.replaceChildren();
  labels.replaceChildren();
  parts.forEach((p) => {
    const seg = html("div", "", undefined, bar);
    seg.style.flex = `${p.value / total} 1 0`;
    seg.style.background = p.color;
    seg.title = `${p.name}: ${p.text}`;
    const l = html("div", "", undefined, labels);
    const v = html("span", "v num", p.text, l);
    const k = html("span", "k", "", l);
    const sw = document.createElement("i");
    sw.style.cssText = `display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;background:${p.color}`;
    k.appendChild(sw);
    if (p.icon) html("span", "status-ico", p.icon, k);
    k.appendChild(document.createTextNode(p.name));
  });
}


// ── API ─────────────────────────────────────────────────────────────────────
// Every call is a JSON POST; every response is the {status, data, message} envelope.
async function postJson(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok || !body || body.status === "error") throw new Error((body && body.message) || `${res.status} ${res.statusText}`);
  return body.data;
}

function list(id, items) {
  const ul = $(id);
  ul.replaceChildren();
  items.forEach((t) => html("li", "", t, ul));
}

function render(DATA) {
  const K = DATA.kpi;
  $("m-n").textContent = num(DATA.n);
  $("m-seed").textContent = DATA.seed;
  $("m-date").textContent = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

  $("findings").replaceChildren();
  for (const f of DATA.findings) {
    const c = html("div", "finding", undefined, $("findings"));
    html("span", "big num", f.value, c);
    html("span", "label", f.label, c);
    html("span", "sub num", f.sub, c);
  }

  const tb = $("inputs");
  tb.replaceChildren();
  for (const r of DATA.inputs) {
    const tr = tb.insertRow();
    tr.insertCell().textContent = r.input;
    tr.insertCell().textContent = r.distribution;
    const range = tr.insertCell(); range.className = "range"; range.textContent = r.range;
    const src = tr.insertCell();
    html("span", "tag " + (r.source.startsWith("Brief") ? "given" : "assumed"), r.source, src);
  }

  $("service-text").textContent = DATA.text.service;
  $("cost-text").textContent = DATA.text.cost;
  $("sizing-text").textContent = DATA.text.sizing;
  $("conv-text").textContent = DATA.text.convergence;

  const SB = DATA.service_bands;
  stack("service-stack", "service-labels", [
    { name: "all deliveries within the shift", value: SB["100%"], text: pct(SB["100%"]), color: "var(--good)", icon: "✓" },
    { name: "95–100% on time", value: SB["95-100%"], text: pct(SB["95-100%"]), color: "var(--warning)", icon: "!" },
    { name: "90–95% on time", value: SB["90-95%"], text: pct(SB["90-95%"]), color: "var(--serious)", icon: "!" },
    { name: "below 90% on time", value: SB["<90%"], text: pct(SB["<90%"]), color: "var(--critical)", icon: "✕" },
  ]);

  const intRange = (a, b) => `${num(Math.ceil(a))}–${num(Math.floor(b))}`;
  histogram("c-vreq", DATA.hist.v_required, {
    aria: "Histogram of vehicles required", xLabel: "vehicles required", fmtX: (v) => num(v),
    fmtRange: (a, b) => `${intRange(a, b)} vehicles`,
    markers: [
      { x: K.v_required_p50, label: `median ${num(K.v_required_p50)}` },
      { x: K.v_use_mean, label: `in service ${num(K.v_use_mean)}`, dash: true },
      { x: K.v_required_p95, label: `P95 ${num(K.v_required_p95)}` },
    ],
  });
  histogram("c-backlog", DATA.hist.backlog, {
    aria: "Histogram of the backlog", xLabel: "open deliveries at end of shift", fmtX: (v) => num(v),
    fmtRange: (a, b) => `${num(a)}–${num(b)} deliveries`, color: "var(--s2)",
    markers: [{ x: K.backlog_p95, label: `P95 ${num(K.backlog_p95)}` }],
  });

  stack("cost-stack", "cost-labels", [
    { name: "Maintenance", value: K.maint_cost_mean, text: eur(K.maint_cost_mean), color: "var(--s1)" },
    { name: "Fuel", value: K.fuel_cost_mean, text: eur(K.fuel_cost_mean), color: "var(--s2)" },
    { name: "Overtime", value: K.overtime_cost_mean, text: eur(K.overtime_cost_mean), color: "var(--s3)" },
  ]);
  const eurRange = (a, b) => `${eur(a)} – ${eur(b)}`;
  const kEur = (v) => `€${num(v / 1000)}k`;
  histogram("c-total", DATA.hist.total_cost, {
    aria: "Histogram of variable daily cost", xLabel: "euros per operating day", fmtX: kEur, fmtRange: eurRange,
    markers: [{ x: K.total_cost_mean, label: `mean ${kEur(K.total_cost_mean)}` }, { x: K.total_cost_p95, label: `P95 ${kEur(K.total_cost_p95)}` }],
  });
  histogram("c-maint", DATA.hist.maint_cost, {
    aria: "Histogram of maintenance cost", xLabel: "euros per operating day", fmtX: kEur, fmtRange: eurRange,
    markers: [{ x: K.maint_cost_mean, label: `mean ${kEur(K.maint_cost_mean)}` }, { x: K.maint_cost_p95, label: `P95 ${kEur(K.maint_cost_p95)}` }],
  });
  const pmf = DATA.breakdowns_pmf;
  histogram("c-break", { edges: pmf.map((_, i) => i - 0.5).concat([pmf.length - 0.5]), counts: pmf }, {
    aria: "Distribution of vehicle breakdowns per day", xLabel: "breakdowns per day", fmtX: (v) => num(v),
    fmtRange: (a) => `${num(a + 0.5)} breakdowns`,
    markers: [{ x: K.breakdowns_mean, label: `mean ${num(K.breakdowns_mean, 1)}` }, { x: 19.5, label: `≥ 20: ${pct(K.p_breakdowns_ge_20)}`, dash: true }],
  });
  histogram("c-fuel", DATA.hist.fuel_l, {
    aria: "Histogram of fuel consumption", xLabel: "litres per operating day", fmtX: (v) => num(v),
    fmtRange: (a, b) => `${num(a)}–${num(b)} L`, color: "var(--s2)",
    markers: [{ x: K.fuel_l_mean, label: `mean ${num(K.fuel_l_mean)} L` }, { x: K.fuel_l_p95, label: `P95 ${num(K.fuel_l_p95)} L` }],
  });

  const S = DATA.sensitivity;
  const sensItems = (key) => Object.entries(S).map(([name, v]) => ({ name, value: v[key] })).sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  diverging("c-sens-short", sensItems("shortfall"), "Rank correlation of inputs with the share of late deliveries");
  diverging("c-sens-cost", sensItems("cost"), "Rank correlation of inputs with daily cost");

  const sz = DATA.sizing;
  lineChart("c-sizing", {
    aria: "Probability of a fully on-time day by fleet size", x: sz.vehicles, y0: 0.5, y1: 1, dots: true,
    xLabel: "available vehicles",
    refs: [{ y: 0.9, label: "example target 90%" }],
    series: [
      { y: sz.drivers_fixed, color: "var(--s1)", endLabel: "pool 520", labelDy: 20 },
      { y: sz.drivers_scaled, color: "var(--s2)", endLabel: "pool grows", labelDy: -12 },
    ],
    tipLines: (i) => [`${num(sz.vehicles[i])} vehicles`, `pool 520: ${pct(sz.drivers_fixed[i])}`, `pool grows: ${pct(sz.drivers_scaled[i])}`],
  });

  const cv = DATA.convergence;
  const lo = Math.min(...cv.lo), hi = Math.max(...cv.hi);
  lineChart("c-conv", {
    aria: "Convergence of the estimate as the number of scenarios grows", x: cv.n,
    y0: Math.max(0, Math.floor(lo * 20) / 20), y1: Math.min(1, Math.ceil(hi * 20) / 20), log: true,
    xLabel: "number of scenarios (log scale)", band: { lo: cv.lo, hi: cv.hi },
    series: [{ y: cv.p, color: "var(--s1)" }],
    tipLines: (i) => [`${num(cv.n[i])} scenarios`, `estimate ${pct(cv.p[i])}`, `CI ${pct(cv.lo[i])} – ${pct(cv.hi[i])}`],
  });

  $("recs").replaceChildren();
  for (const r of DATA.recommendations) {
    const el2 = html("div", "rec", undefined, $("recs"));
    html("h3", "", r.title, el2);
    html("p", "", r.text, el2);
  }
  list("limitations", DATA.limitations);
  list("next-steps", DATA.next_steps);
}

// ── controls ───────────────────────────────────────────────────────────────
let current = null;   // request of the report on screen; downloads use exactly this run

function request() {
  return { n: Math.round(Number($("in-n").value)), seed: Math.round(Number($("in-seed").value)) };
}

function setDownloads(enabled) {
  ["dl-pdf", "dl-xlsx"].forEach((id) => { $(id).disabled = !enabled; });
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

async function load() {
  const req = request();
  showError("");
  $("status").hidden = false;
  $("status").textContent = `Simulating ${num(req.n)} operating days…`;
  $("run").disabled = true;
  setDownloads(false);
  try {
    render(await postJson("/v1/reports/fleet", req));
    current = req;
    setDownloads(true);
    $("status").hidden = true;
    try { localStorage.setItem("fleet-report", JSON.stringify(req)); } catch { /* storage unavailable */ }
  } catch (err) {
    $("status").hidden = true;
    showError(`The report could not be built: ${err.message}. Check the scenario count (1,000–50,000) and the seed, then run it again.`);
  } finally {
    $("run").disabled = false;
  }
}

// The file arrives inside the JSON response as base64; turn it into a download.
function saveFile(file) {
  const bytes = Uint8Array.from(atob(file.content), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: file.content_type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = file.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function download(kind, button) {
  if (!current) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = kind === "pdf" ? "Building PDF…" : "Building Excel…";
  try {
    saveFile(await postJson(`/v1/reports/fleet/${kind}`, current));
    showError("");
  } catch (err) {
    showError(`The ${kind === "pdf" ? "PDF" : "Excel file"} could not be created: ${err.message}.`);
  } finally {
    button.textContent = label;
    button.disabled = false;
  }
}

$("run").addEventListener("click", load);
$("dl-pdf").addEventListener("click", (e) => download("pdf", e.currentTarget));
$("dl-xlsx").addEventListener("click", (e) => download("xlsx", e.currentTarget));
try {
  const saved = JSON.parse(localStorage.getItem("fleet-report") || "null");
  if (saved && saved.n) $("in-n").value = saved.n;
  if (saved && saved.seed !== undefined) $("in-seed").value = saved.seed;
} catch { /* storage unavailable or old format */ }
load();
