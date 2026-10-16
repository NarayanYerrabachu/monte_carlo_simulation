// Job report (/report?job=<id>, or the newest finished job): the assumption-report format of the
// fleet demo, built from a scenario job's result. Helpers (num, pct, histogram, …) come from
// report_charts.js. Fleet jobs open the fleet dashboard instead (their report lives there).
const POLL_MS = 1500, ACTIVE = ["queued", "running"];
const state = { jobId: null, job: null, result: null, timer: null };

async function api(path, init) {
  const res = await fetch(path, { headers: { Accept: "application/json", "Content-Type": "application/json" }, ...init });
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (!res.ok || !body || body.status === "error") throw new Error((body && body.message) || `${res.status} ${res.statusText}`);
  return body.data;
}
function showError(msg) { $("error").hidden = !msg; $("error").textContent = msg || ""; }
function status(msg) { $("status").hidden = !msg; $("status").textContent = msg || ""; }
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }
// a value in the format the job asked for; small numbers keep their digits
function fv(v, fmt, unit) {
  if (v == null || !isFinite(v)) return "–";
  if (fmt === "pct") return pct(v);
  const a = Math.abs(v);
  const t = fmt === "int" || a >= 1000 ? num(v) : a >= 1 ? num(v, 2) : a >= 0.1 ? num(v, 3) : a > 0 ? num(v, 4) : "0";
  return unit ? `${t} ${unit}` : t;
}

// ── render ──────────────────────────────────────────────────────────────────
function render(sc, job) {
  const s = sc.summary, k = s.kpi, cfg = sc.config, m = s.metrics;
  const period = cfg.period_label, unit = s.dashboard.unit, records = cfg.record_label;
  const title = cfg.title || "Scenario simulation";
  const assumed = {};
  (s.inputs.assumptions || []).forEach((a) => { assumed[a.key] = a; });
  const nAssumed = Object.keys(assumed).length;

  $("h-eyebrow").textContent = `${title} · Monte Carlo on TDA / ML results`;
  $("h-title").textContent = `Monte Carlo Simulation with TDA: ${title}`;
  $("h-lede").textContent = `From “what might happen?” to “how likely is it?”: instead of one point estimate this report runs through ${num(sc.n_completed)} possible ${unit}, each resampled from the real ${records} and the regimes TDA found in them.`;
  $("m-n").textContent = num(sc.n_completed);
  $("m-unit").textContent = `simulated ${unit}`;
  $("m-seed").textContent = cfg.seed;
  $("m-date").textContent = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  $("m-inputs").textContent = nAssumed ? `Inputs: the data's own values, plus ${nAssumed} marked assumption${nAssumed > 1 ? "s" : ""}` : "Inputs: the data's own values, no assumptions";
  $("nav-live").href = `/?job=${encodeURIComponent(job.job_id)}`;
  $("nav-dash").href = `/fleet?job=${encodeURIComponent(job.job_id)}`;

  $("summary-text").textContent = (s.text || {}).results || "";
  $("findings").replaceChildren();
  (s.findings || []).forEach((f) => {
    const c = html("div", "finding", undefined, $("findings"));
    html("span", "big num", f.value, c); html("span", "label", f.label, c); html("span", "sub num", f.sub, c);
  });

  const pr = s.predictions;
  $("pred-section").hidden = !(pr && pr.rows && pr.rows.length);
  if (pr && pr.rows && pr.rows.length) {
    $("h-pred").textContent = `What to expect in the next ${pr.horizon}`;
    $("pred-text").textContent = pr.text || "";
    $("pred-note").textContent = pr.note || "";
    $("pred-h-exp").textContent = `Expected next ${pr.horizon}`;
    $("pred-h-p").textContent = `P(breach) next ${pr.horizon}`;
    $("pred-h-recent").textContent = `Recent ${unit}`;
    const pb = $("pred-rows"); pb.replaceChildren();
    pr.rows.forEach((r) => {
      const row = pb.insertRow();
      [r.label, fv(r.expected, r.fmt, r.unit), `${fv(r.low, r.fmt, r.unit)} – ${fv(r.high, r.fmt, r.unit)}`, r.alert == null ? "–" : fv(r.alert, r.fmt, r.unit),
       r.p_over == null ? "–" : pct(r.p_over), r.p_over_20 == null ? "–" : pct(r.p_over_20), r.drift || "–", r.outlook]
        .forEach((v, i) => { const td = row.insertCell(); td.textContent = v; if (i === 6 && (v === "above" || v === "below")) td.style.color = "var(--s2)"; });
    });
    if (pr.high) {
      const row = pb.insertRow();
      [`High-risk ${records}`, (pr.high.expected || 0).toFixed(1), `0 – ${num(pr.high.p95)}`, "–", pct(pr.high.p_any), `${pct(pr.high.p_any_5)} (in 5)`, "–", "records the anomaly model flags"]
        .forEach((v) => { row.insertCell().textContent = v; });
    }
  }

  const tb = $("inputs"); tb.replaceChildren();
  let tr = tb.insertRow();
  tr.insertCell().textContent = `${cap(records)} in the data`;
  tr.insertCell().textContent = cfg.n_periods ? `${num(cfg.n_records)} · ${num(cfg.n_periods)} ${unit}` : num(cfg.n_records);
  tr.insertCell().textContent = cfg.n_pool < cfg.n_records ? `${num(cfg.n_pool)} in the sampling pool` : "–";
  tr.insertCell().textContent = "–";
  html("span", "tag given", "Data", tr.insertCell());
  m.forEach((x) => {
    const o = (s.inputs.observed || {})[x.key] || s.inputs.metrics[x.key], a = assumed[x.key];
    tr = tb.insertRow();
    tr.insertCell().textContent = x.label + (x.unit ? ` (${x.unit})` : "");
    const range = tr.insertCell(); range.className = "range";
    range.textContent = o ? `${fv(o.p5, x.record_fmt)} – ${fv(o.p95, x.record_fmt)}` : "–";
    const parts = [];
    if (a && (a.low != null || a.high != null)) parts.push(`${a.low != null ? fv(a.low, x.record_fmt) : "…"} – ${a.high != null ? fv(a.high, x.record_fmt) : "…"}`);
    if (a && a.shift) parts.push(`shift ${a.shift > 0 ? "+" : ""}${fv(a.shift, x.record_fmt)}`);
    if (a && a.records_removed) parts.push(`${num(a.records_removed)} ${records} outside`);
    tr.insertCell().textContent = parts.join(" · ") || "–";
    tr.insertCell().textContent = x.alert != null ? fv(x.alert, x.fmt, x.unit) : "–";
    const src = tr.insertCell();
    html("span", "tag " + (parts.length ? "assumed" : "given"), parts.length ? "Assumed" : "Data", src);
  });
  $("inputs-text").hidden = false;

  $("h-steps").textContent = `How one ${period} is calculated`;
  list("steps", [
    cfg.n_periods ? `One of the ${num(cfg.n_periods)} historical ${unit} is drawn at random, and ${num(cfg.period_size)} of its ${records} are resampled with replacement.`
                  : `${num(cfg.period_size)} ${records} are resampled with replacement from the whole dataset (it has no usable periods).`,
    `Each metric is aggregated over them: ${m.map((x) => `${x.label} as ${x.agg === "sum" ? "a total" : "an average"}`).join(", ")}.`,
    s.anomalies.high_risk ? `The ${records} with an ML anomaly score of ${cfg.high_anomaly} or more are counted as high-risk.` : "The ML anomaly scores were not available, so no high-risk count is kept.",
    m.some((x) => x.alert != null) ? `Each metric is compared with its alert threshold (sent with the job or the 90th percentile of the historical ${unit}).` : "No alert thresholds were available; set them in the what-if box.",
    `This is repeated ${num(sc.n_completed)} times${cfg.exclude_anomalies_above != null ? `, without the ${num(s.anomalies.excluded)} records the anomaly filter removed` : ""}; the shares of scenarios are the probabilities below.`,
  ]);

  $("outcomes-text").textContent = `${cap(unit)} vary because the records they are made of vary. The charts show how often each level occurs; the dashed lines mark the expected value, the 95th percentile and the alert.`;
  const charts = $("charts"); charts.replaceChildren();
  const cards = [];
  m.forEach((x) => {
    const card = html("div", "chart-card", undefined, charts);
    html("h3", "", `${x.label} per ${period}`, card);
    html("p", "note", `${x.agg === "sum" ? "Total" : "Average"} over the ${num(cfg.period_size)} ${records} of a ${period}` + (x.unit ? ` (${x.unit})` : ""), card);
    const box = html("div", "chart", undefined, card); box.id = `c-${x.key}`;
    cards.push([x, box.id]);
  });
  let highId = null;
  if (s.anomalies.high_risk) {
    const card = html("div", "chart-card", undefined, charts);
    html("h3", "", `High-risk ${records} per ${period}`, card);
    html("p", "note", `${cap(records)} with an anomaly score of ${cfg.high_anomaly} or more`, card);
    highId = html("div", "chart", undefined, card).id = "c-high";
  }
  cards.forEach(([x, id]) => {
    const markers = [{ x: k[`${x.key}_mean`], label: `expected ${fv(k[`${x.key}_mean`], x.fmt, x.unit)}` },
                     { x: k[`${x.key}_p95`], label: `P95 ${fv(k[`${x.key}_p95`], x.fmt, x.unit)}` }];
    if (x.alert != null) markers.push({ x: x.alert, label: `alert ${fv(x.alert, x.fmt, x.unit)}`, dash: true });
    histogram(id, s.hist[x.key], { aria: `Histogram of ${x.label} per ${period}`, xLabel: `${x.label} per ${period}`, unit,
      fmtX: (v) => fv(v, x.fmt), fmtRange: (a, b) => `${fv(a, x.fmt, x.unit)} – ${fv(b, x.fmt, x.unit)}`, markers });
  });
  if (highId) histogram(highId, s.hist.high, { aria: `Histogram of high-risk ${records} per ${period}`, xLabel: `high-risk ${records} per ${period}`, unit,
    fmtX: (v) => num(v), fmtRange: (a, b) => `${num(Math.ceil(a))}–${num(Math.floor(b))} ${records}`, color: "var(--s2)",
    markers: [{ x: k.high_mean, label: `expected ${num(k.high_mean, 1)}` }, { x: k.high_p95, label: `P95 ${num(k.high_p95)}` }] });

  const tails = s.tails || [];
  const rt = $("regimes"); rt.replaceChildren();
  const head = rt.createTHead().insertRow();
  ["Regime (TDA cluster)", `${cap(records)}`, "Share", "Avg anomaly", "High-risk share", ...m.slice(0, 3).map((x) => x.label),
   ...tails.map((t) => `In high-${t.label} ${unit}`)].forEach((h) => html("th", "", h, head));
  const body = rt.createTBody();
  [...sc.results].sort((a, b) => (b.high_risk_share || 0) * b.share - (a.high_risk_share || 0) * a.share).slice(0, 12).forEach((g) => {
    const r = body.insertRow();
    [g.label, num(g.records), pct(g.share), g.anomaly_mean == null ? "–" : num(g.anomaly_mean, 3), g.high_risk_share == null ? "–" : pct(g.high_risk_share),
     ...m.slice(0, 3).map((x) => fv(g.metrics[x.key], x.record_fmt)),
     ...tails.map((t) => (g.tail_lift[t.key] == null ? "–" : `×${g.tail_lift[t.key].toFixed(2)}`))].forEach((v) => { r.insertCell().textContent = v; });
  });
  $("regimes-text").textContent = `Each regime is a cluster TDA found in the ${records}. The simulation samples from all of them; the table shows how each behaves`
    + (tails.length ? `, and how over-represented it is in the ${unit} where a metric is in its top 5% (×1 = as often as usual). A regime far above ×1 is a leading indicator.` : ".");

  const drivers = (s.sensitivity || []).filter((d) => d.alerts != null || d.high != null);
  $("drivers-section").hidden = !drivers.length;
  if (drivers.length) {
    const withAlerts = drivers.some((d) => d.alerts != null);
    $("h-drivers").textContent = `What drives the risk in ${title}`;
    $("drivers-title").textContent = withAlerts ? `Drivers of alerts per ${period}` : `Drivers of high-risk ${records} per ${period}`;
    $("drivers-text").textContent = `Correlation of each input's value in a ${period} with ${withAlerts ? "the number of alerts" : `the number of high-risk ${records}`} in that ${period}, over all scenarios. Values near 0 mean the input hardly moves the risk; a negative value means a higher input goes with fewer ${withAlerts ? "alerts" : `high-risk ${records}`}. ` + ((s.text || {}).drivers || "");
    diverging("c-drivers", drivers.map((d) => ({ name: d.label, value: d.alerts != null ? d.alerts : d.high })).sort((a, b) => Math.abs(b.value) - Math.abs(a.value)),
      "Correlation of inputs with the risk per period");
  }

  // what-if box
  $("lbl-period").textContent = `${cap(records)} per ${period}`;
  $("in-period").value = cfg.period_size;
  $("in-sims").value = sc.n_completed;
  $("in-clean").checked = cfg.exclude_anomalies_above != null;
  const at = $("assume-table").tBodies[0]; at.replaceChildren();
  m.forEach((x) => {
    const o = (s.inputs.observed || {})[x.key] || s.inputs.metrics[x.key], a = assumed[x.key] || {};
    const r = at.insertRow();
    r.insertCell().textContent = x.label + (x.unit ? ` (${x.unit})` : "");
    r.insertCell().textContent = o ? `${fv(o.p5, x.record_fmt)} – ${fv(o.p95, x.record_fmt)}` : "–";
    [["low", a.low], ["high", a.high], ["shift", a.shift], ["alert", x.alert]].forEach(([field, v]) => {
      const inp = document.createElement("input");
      inp.type = "number"; inp.step = "any"; inp.dataset.key = x.key; inp.dataset.field = field;
      inp.value = v == null ? "" : Number(Number(v).toPrecision(4));
      inp.placeholder = field === "alert" ? "P90 of history" : "–";
      r.insertCell().appendChild(inp);
    });
  });

  const cv = s.convergence, m0 = m[0];
  if (cv.n && cv.n.length) {
    const lo = Math.min(...cv.lo), hi = Math.max(...cv.hi), pad = (hi - lo) * 0.3 || Math.abs(hi) * 0.05 || 1;
    lineChart("c-conv", { aria: "Convergence of the estimate as the number of scenarios grows", x: cv.n, y0: lo - pad, y1: hi + pad, log: true,
      xLabel: "number of scenarios (log scale)", band: { lo: cv.lo, hi: cv.hi }, series: [{ y: cv.p, color: "var(--s1)" }],
      fmtY: (v) => fv(v, m0.fmt), tipLines: (i) => [`${num(cv.n[i])} scenarios`, `estimate ${fv(cv.p[i], m0.fmt, m0.unit)}`, `CI ${fv(cv.lo[i], m0.fmt, m0.unit)} – ${fv(cv.hi[i], m0.fmt, m0.unit)}`] });
    $("conv-label").textContent = cv.label || "estimate";
  } else { $("c-conv").replaceChildren(); }
  $("conv-text").textContent = (s.text || {}).quality || "";
  const bt = s.backtest;
  $("backtest-text").textContent = bt && bt.available !== false && bt.verdict
    ? `Backtest: simulated from the first 80% of the ${unit} and compared with the last 20%, the simulation ${bt.verdict}${bt.coverage != null ? ` (${pct(bt.coverage, 0)} of the held-out values inside the simulated 90% band)` : ""}.`
    : (bt && bt.reason ? `Backtest not possible: ${bt.reason}.` : "");

  $("recs").replaceChildren();
  (s.recommendations || []).forEach((r) => { const e = html("div", "rec", undefined, $("recs")); html("h3", "", r.title, e); html("p", "", r.text, e); });
  list("limitations", [
    `Resampling can only produce values the data already contains: a ${period} worse than any record seen is outside the simulation.`,
    cfg.n_periods ? `The ${unit} are real historical ones; a shift in the market or the process after the data ends is not in the ranges.` : `The data has no usable periods, so every scenario mixes records from the whole dataset.`,
    nAssumed ? "Assumed ranges make the results conditional: they hold only if the inputs stay inside them." : "The ML anomaly scores and TDA regimes come from CortXplorer's analysis; their quality bounds the regime and high-risk statements.",
    "Correlations in the drivers chart show association over the scenarios, not cause and effect.",
  ]);
  list("next-steps", [
    "Set alert thresholds that match your targets in the what-if box and re-run; the probabilities of breaching them are the risk figures to plan with.",
    "Try a shift on the strongest driver to see how much of the risk it explains.",
    `Check the ${records} of the regime most over-represented in the bad ${unit}; TDA found them before the period closed.`,
    "Repeat the run when new data arrives; the backtest says whether the past still predicts the present.",
  ]);
  $("report").hidden = false;
}

// ── jobs ────────────────────────────────────────────────────────────────────
async function refreshJobs(selectId) {
  const jobs = (await api("/v1/jobs")).filter((j) => j.tests.some((t) => t === "fleet" || t === "scenario"));
  const sel = $("job-select"); sel.replaceChildren();
  if (!jobs.length) sel.add(new Option("— no jobs yet —", ""));
  jobs.forEach((j) => sel.add(new Option(`${new Date(j.created * 1000).toLocaleTimeString()} · ${j.dataset_id} · ${j.status}`, j.job_id)));
  if (selectId && jobs.some((j) => j.job_id === selectId)) sel.value = selectId;
  $("empty").hidden = jobs.length > 0;
  return jobs;
}
function selectJob(jobId) {
  clearTimeout(state.timer);
  state.jobId = jobId || null;
  $("report").hidden = true;
  setDownloads(false);
  const url = new URL(location.href);
  if (jobId) url.searchParams.set("job", jobId); else url.searchParams.delete("job");
  history.replaceState(null, "", url);
  if (jobId) poll();
}
async function poll() {
  try {
    const st = await api(`/v1/jobs/${state.jobId}`);
    if (st.tests.includes("fleet") && !st.tests.includes("scenario")) { location.replace(`/fleet?job=${encodeURIComponent(state.jobId)}`); return; }
    if (ACTIVE.includes(st.status)) {
      const p = (st.progress && st.progress.scenario) || { done: 0, total: 0 };
      status(p.total ? `Simulating: ${num(p.done)} / ${num(p.total)}…` : `Job ${st.status}…`);
      state.timer = setTimeout(poll, POLL_MS);
      return;
    }
    status("");
    if (st.status === "cancelled") { showError("This run was cancelled."); return; }
    const res = await api(`/v1/jobs/${state.jobId}/result`);
    if (!res.scenario) { showError(`The simulation failed: ${Object.values(res.errors || {}).join("; ") || "no result"}`); return; }
    state.job = st; state.result = res;
    render(res.scenario, st);
    setDownloads(true);
    showError("");
  } catch (e) { status(""); showError(e.message); }
}

// ── what-if ─────────────────────────────────────────────────────────────────
function readAssumptions() {
  const assumptions = {}, alerts = {};
  document.querySelectorAll("#assume-table input").forEach((inp) => {
    if (inp.value === "") return;
    const v = Number(inp.value);
    if (!isFinite(v)) return;
    if (inp.dataset.field === "alert") alerts[inp.dataset.key] = v;
    else (assumptions[inp.dataset.key] = assumptions[inp.dataset.key] || {})[inp.dataset.field] = v;
  });
  return { assumptions, alerts };
}
async function rerun() {
  if (!state.jobId) return;
  const { assumptions, alerts } = readAssumptions();
  const scenario = { exclude_anomalies_above: $("in-clean").checked ? 0.6 : null, assumptions };
  if (Number($("in-period").value)) scenario.period_size = Number($("in-period").value);
  if (Object.keys(alerts).length) scenario.alerts = alerts;
  $("rerun").disabled = true;
  $("rerun-status").hidden = false; $("rerun-status").textContent = "Starting the run…";
  try {
    const job = await api(`/v1/jobs/${state.jobId}/rerun`, { method: "POST", body: JSON.stringify({ n_sims: Number($("in-sims").value) || null, scenario }) });
    await refreshJobs(job.job_id);
    $("rerun-status").hidden = true;
    selectJob(job.job_id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) {
    $("rerun-status").textContent = `Re-run failed: ${e.message}`;
  } finally { $("rerun").disabled = false; }
}

// ── downloads ───────────────────────────────────────────────────────────────
function setDownloads(on) { ["dl-pdf", "dl-xlsx"].forEach((id) => { $(id).disabled = !on; }); }
async function download(format, button) {
  if (!state.jobId) return;
  const label = button.textContent;
  button.disabled = true; button.textContent = format === "pdf" ? "Building PDF…" : "Building Excel…";
  try {
    const f = await api("/v1/reports/job", { method: "POST", body: JSON.stringify({ job_id: state.jobId, format }) });
    const bytes = Uint8Array.from(atob(f.content), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: f.content_type }));
    const a = document.createElement("a"); a.href = url; a.download = f.filename; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { showError(`The ${format === "pdf" ? "PDF" : "Excel file"} could not be created: ${e.message}`); }
  finally { button.textContent = label; button.disabled = false; }
}

async function init() {
  $("job-select").addEventListener("change", (e) => selectJob(e.target.value));
  $("rerun").addEventListener("click", rerun);
  $("dl-pdf").addEventListener("click", (e) => download("pdf", e.currentTarget));
  $("dl-xlsx").addEventListener("click", (e) => download("xlsx", e.currentTarget));
  try { await api("/health"); } catch (e) { showError(`Service unreachable: ${e.message}`); return; }
  try {
    const jobs = await refreshJobs();
    const wanted = new URLSearchParams(location.search).get("job");
    const linked = wanted && jobs.find((j) => j.job_id === wanted);
    if (wanted && !linked) showError(`Job ${wanted} is not available (it expired, or it was started before the service kept jobs across restarts). Press Monte Carlo ↗ in CortXplorer to run the simulation again.`);
    const first = linked || jobs[0];
    if (first) { $("job-select").value = first.job_id; selectJob(first.job_id); }
  } catch (e) { showError(e.message); }
}
init();
