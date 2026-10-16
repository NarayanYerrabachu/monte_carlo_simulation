// Fleet demo page: fetches /v1/reports/fleet and renders it; PDF and Excel come from the same run.
// Formatting, chart and API helpers come from report_charts.js (loaded first).

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
