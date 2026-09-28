// Monte Carlo live viewer. Polls GET /v1/jobs/{id}/live and draws what the
// service sends: the observed cloud, the surrogate of the latest simulation,
// the growing null distribution and the server-computed running p-value and
// noise band. Statistics are never computed here.

const POLL_MS = 350;
const ACTIVE = ["queued", "running"];

const $ = (id) => document.getElementById(id);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const state = {
  test: "loops",     // "loops" | "fleet" — which live feed the job has
  jobId: null,
  null: [],          // null statistic per simulation, in order
  observed: null,    // {stat, alpha, points, heuristic_threshold, statistic}
  frame: null,       // {index, points}
  running: null,     // {n, p_value, noise_band}
  status: null,
  timer: null,
  generation: 0,     // bumps on job switch so stale polls are ignored
  plotReady: false,
  eye: { angle: Math.PI / 4, r: 2.1, z: 1.1 },   // camera orbit; rotation changes angle
};

const eyeXYZ = () => ({ x: state.eye.r * Math.cos(state.eye.angle), y: state.eye.r * Math.sin(state.eye.angle), z: state.eye.z });

// ── API ─────────────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok || !body || body.status === "error") {
    throw new Error((body && body.message) || `${res.status} ${res.statusText}`);
  }
  return body.data;
}

function showError(message) {
  const el = $("error");
  el.textContent = message;
  el.hidden = !message;
}

// ── Formatting ──────────────────────────────────────────────────────────────
const fmt = (v, digits = 3) => (v === null || v === undefined ? "–" : Number(v).toFixed(digits));
const fmtP = (p) => (p === null || p === undefined ? "–" : p < 0.001 ? p.toExponential(1) : p.toFixed(3));

// ── Synthetic data (seeded, so a rerun is identical) ────────────────────────
function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function synthetic(shape, n = 1200) {
  const rand = mulberry32(7);
  const gauss = () => Math.sqrt(-2 * Math.log(rand() + 1e-12)) * Math.cos(2 * Math.PI * rand());
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = 2 * Math.PI * rand();
    let p;
    if (shape === "circle") {
      const [x, y] = [Math.cos(t), Math.sin(t)];
      const tilt = 0.35;                              // tilt the ring slightly out of the xy-plane
      p = [x, y * Math.cos(tilt), y * Math.sin(tilt)].map((v) => v + 0.08 * gauss());
    } else if (shape === "two") {
      p = i % 2 === 0
        ? [-1.4 + Math.cos(t), Math.sin(t), 0]        // ring in the xy-plane
        : [1.4 + Math.cos(t), 0, Math.sin(t)];        // ring in the xz-plane
      p = p.map((v) => v + 0.07 * gauss());
    } else {
      const [g1, g2, g3] = [gauss(), gauss(), gauss()];
      p = [g1, 0.8 * g1 + 0.6 * g2, 0.4 * g3].map((v) => 0.8 * v);
    }
    pts.push(p.map((v) => Math.round(v * 1e4) / 1e4));
  }
  return pts;
}

// ── Plots ───────────────────────────────────────────────────────────────────
function baseLayout() {
  const ink = cssVar("--muted");
  const grid = cssVar("--line");
  const axis = () => ({ showbackground: false, gridcolor: grid, zerolinecolor: grid, color: ink, title: { text: "" } });
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: cssVar("--ink"), size: 12 },
    margin: { l: 0, r: 0, t: 8, b: 0 },
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, bgcolor: "rgba(0,0,0,0)" },
    scene: { xaxis: axis(), yaxis: axis(), zaxis: axis(), aspectmode: "data", camera: { eye: eyeXYZ() } },
    uirevision: "keep",
  };
}

function cloudTrace(points, name, color, opacity) {
  return {
    type: "scatter3d", mode: "markers", name,
    x: points.map((p) => p[0]), y: points.map((p) => p[1]), z: points.map((p) => p[2]),
    marker: { size: 2.6, color, opacity },
    hoverinfo: "skip",
  };
}

// Categorical colours for TDA regimes (fixed order); highlight = the day being simulated
const REGIME_COLORS = ["#2a78d6", "#1baf7a", "#e87ba4", "#4a3aa7", "#008300", "#eda100", "#e34948", "#8a8f98"];

function regimeTraces(obs, frame) {
  const pts = obs.points, groups = obs.groups;
  const ids = [...new Set(groups)].sort((a, b) => a - b);
  const traces = ids.map((g, i) => {
    const sel = pts.filter((_, k) => groups[k] === g);
    const name = (obs.group_labels && obs.group_labels[String(g)]) || `Regime ${g}`;
    const t = cloudTrace(sel, name, g === -1 ? "#8a8f98" : REGIME_COLORS[i % (REGIME_COLORS.length - 1)], 0.35);
    t.marker.size = 2.4;
    return t;
  });
  if (frame && frame.highlight) {
    const hi = frame.highlight.map((k) => pts[k]);
    const t = cloudTrace(hi, `Vehicles of simulated day ${frame.index + 1} (${frame.day})`, cssVar("--surrogate"), 0.95);
    t.marker.size = 5;
    traces.push(t);
  }
  return traces;
}

function render3d() {
  const obs = state.observed;
  let traces = [];
  if (obs && obs.groups) {
    traces = regimeTraces(obs, state.frame);
  } else {
    if (obs) traces.push(cloudTrace(obs.points, "Observed data", cssVar("--observed"), 0.85));
    if (state.frame && state.frame.points) {
      traces.push(cloudTrace(state.frame.points, `Surrogate · simulation ${state.frame.index + 1}`,
        cssVar("--surrogate"), 0.55));
    }
  }
  if (!traces.length) return;
  const layout = baseLayout();
  if (obs && obs.axis_titles) {
    ["xaxis", "yaxis", "zaxis"].forEach((a, i) => { layout.scene[a].title = { text: obs.axis_titles[i] }; });
    layout.scene.aspectmode = "cube";
    layout.legend = { orientation: "h", x: 0, y: 0, yanchor: "top", bgcolor: "rgba(0,0,0,0)", font: { size: 11 } };
    layout.margin = { l: 0, r: 0, t: 8, b: 70 };
  }
  Plotly.react("plot3d", traces, layout, { displaylogo: false, responsive: true });
  state.plotReady = true;
}

function vline(x, color, dash, label) {
  return {
    shape: { type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1, line: { color, width: 2.2, dash } },
    note: { x, xref: "x", yref: "paper", y: 1, yanchor: "bottom", text: label, showarrow: false,
            font: { size: 11, color } },
  };
}

function renderHist() {
  const muted = cssVar("--muted");
  const grid = cssVar("--line");
  const lines = [];
  const obs = state.observed;
  if (obs && obs.stat !== null) lines.push(vline(obs.stat, cssVar("--observed"), "solid", obs.stat_label || "observed"));
  if (state.running && state.running.noise_band !== null) {
    lines.push(vline(state.running.noise_band, cssVar("--band"), "dash",
      obs.band_label || `${Math.round((1 - obs.alpha) * 100)}% band`));
  }
  if (obs && obs.heuristic_threshold !== null && obs.heuristic_threshold !== undefined) {
    lines.push(vline(obs.heuristic_threshold, muted, "dot", "heuristic"));
  }
  const layout = {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: cssVar("--ink"), size: 12 },
    margin: { l: 44, r: 12, t: 24, b: 40 },
    bargap: 0.04, showlegend: false,
    xaxis: { title: { text: obs ? obs.statistic : "", font: { size: 11, color: muted } }, gridcolor: grid, zeroline: false, color: muted },
    yaxis: { title: { text: "simulations", font: { size: 11, color: muted } }, gridcolor: grid, zeroline: false, color: muted },
    shapes: lines.map((l) => l.shape),
    annotations: lines.map((l) => l.note),
    uirevision: "keep",
  };
  const finite = state.null.filter((v) => v !== null);
  const lo = finite.length ? Math.min(...finite) : 0;
  const hi = finite.length ? Math.max(...finite) : 1;
  const size = hi > lo ? (hi - lo) / 30 : 0.01;
  const trace = { type: "histogram", x: finite, autobinx: false, xbins: { start: lo, end: hi + size, size },
                  marker: { color: cssVar("--surrogate"), opacity: 0.75 }, hovertemplate: "%{y} sims<extra></extra>" };
  Plotly.react("plothist", [trace], layout, { displaylogo: false, responsive: true });
}

function renderStats(view) {
  const prog = (view.progress && view.progress[state.test]) || { done: 0, total: 0 };
  $("st-n").textContent = prog.total ? `${prog.done} / ${prog.total}` : "–";
  $("progress-bar").style.width = prog.total ? `${(100 * prog.done) / prog.total}%` : "0";

  const r = state.running || {};
  const obs = state.observed || {};
  const alpha = obs.alpha != null ? obs.alpha : 0.05;
  const pEl = $("st-p");
  const finished = !ACTIVE.includes(view.status);
  if (obs.mode === "share_at_least") {                // fleet: P(day meets the SLA)
    $("lbl-p").textContent = obs.running_label || "P(meet target)";
    $("lbl-band").textContent = obs.band_label || "Worst days";
    $("lbl-top").textContent = obs.headline_label || "Target";
    pEl.textContent = r.share_at_least == null ? "–" : `${(r.share_at_least * 100).toFixed(1)}%`;
    pEl.className = "value" + (!finished || r.share_at_least == null ? "" : r.share_at_least >= 0.9 ? " good" : " bad");
    $("st-band").textContent = r.noise_band == null ? "–" : `${(r.noise_band * 100).toFixed(1)}%`;
    $("st-top").textContent = obs.headline == null ? "–" : String(obs.headline);
  } else {
    $("lbl-p").textContent = "Running p-value";
    $("lbl-band").textContent = "Noise band";
    $("lbl-top").textContent = "Longest loop";
    pEl.textContent = fmtP(r.p_value);
    pEl.className = "value" + (!finished || r.p_value === null || r.p_value === undefined ? "" : r.p_value <= alpha ? " good" : " bad");
    $("st-band").textContent = fmt(r.noise_band);
    $("st-top").textContent = fmt(obs.stat);
  }
  $("hist-sub").textContent = r.n ? `· ${r.n} simulations` : "";

  const errs = Object.entries(view.errors || {}).map(([t, e]) => `${t}: ${e}`).join(" · ");
  $("status-line").textContent =
    `${view.status} · ${view.elapsed_s.toFixed(1)} s · ${view.dataset_id}` + (errs ? ` · ${errs}` : "");
  $("cancel").disabled = !ACTIVE.includes(view.status);
}

function renderTable(loops, emptyText = "No loop result for this job.") {
  const body = $("loops-table").querySelector("tbody");
  body.replaceChildren();
  const rows = loops ? loops.results.slice(0, 15) : [];
  if (!rows.length) {
    const tr = body.insertRow();
    const td = tr.insertCell();
    td.colSpan = 6; td.className = "muted";
    td.textContent = loops ? "No loops in the observed sample." : emptyText;
  }
  for (const r of rows) {
    const tr = body.insertRow();
    [r.index + 1, fmt(r.birth), fmt(r.death), fmt(r.persistence), fmtP(r.p_value)].forEach((v) => {
      tr.insertCell().textContent = v;
    });
    const pill = document.createElement("span");
    pill.className = "pill " + (r.significant ? "good" : "none");
    pill.textContent = r.significant ? "significant" : "noise";
    tr.insertCell().appendChild(pill);
  }
  const s = loops && loops.summary;
  $("verdict").textContent = s
    ? `· ${s.n_significant} significant of ${s.n_loops_observed} observed` + (loops.stopped_early ? " · stopped early (time budget)" : "")
    : "";
}

// ── Polling ─────────────────────────────────────────────────────────────────
// ── Fleet: the sample dashboard, filled day by day ─────────────────────────
// The service streams every simulated day's values and running KPIs (computed
// server-side at each checkpoint). A fast run is replayed over a few seconds so
// the distributions can be seen building up; tiles and lines always show the
// server's KPI for the number of days revealed so far.
const REVEAL_STEPS = 80, REVEAL_MS = 90;
const fl = { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false };

function resetFleet() {
  clearInterval(fl.timer);
  Object.assign(fl, { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false });
  $("fl-report").hidden = true;
  $("fl-replay").disabled = true;
  ["fl-vehicles", "fl-outcome", "fl-maint", "fl-share"].forEach((id) => Plotly.purge(id));
}

function flReceive(feed, total) {
  for (const [k, v] of Object.entries(feed.series || {})) (fl.series[k] = fl.series[k] || []).push(...v);
  fl.series.share = (fl.series.share || []).concat(feed.null || []);
  fl.checkpoints = feed.checkpoints || fl.checkpoints;
  fl.total = total || fl.total;
  if (!fl.timer && fl.revealed < (fl.series.share || []).length) startReveal();
}

function startReveal() {
  clearInterval(fl.timer);
  const step = Math.max(1, Math.ceil((fl.total || 1) / REVEAL_STEPS));
  fl.timer = setInterval(() => {
    const received = (fl.series.share || []).length;
    fl.revealed = Math.min(received, fl.revealed + step);
    renderFleetLive();
    if (fl.revealed >= received) {
      clearInterval(fl.timer);
      fl.timer = null;
      if (fl.done) finishFleet();
    }
  }, REVEAL_MS);
}

function finishFleet() {
  $("fl-replay").disabled = false;
  const btn = $("fl-report");
  btn.href = `/report?job=${encodeURIComponent(state.jobId)}`;
  btn.hidden = false;
}

function checkpointAt(n) {
  let best = null;
  for (const c of fl.checkpoints) if (c.n <= n) best = c;
  return best || fl.checkpoints[0] || null;
}

function flHist(id, values, color, xTitle, lines, xfmt) {
  const all = values.all.filter((v) => v != null && Number.isFinite(v));
  const shown = values.shown.filter((v) => v != null && Number.isFinite(v));
  if (!all.length) return;
  const lo = Math.min(...all), hi = Math.max(...all);
  const whole = all.every((v) => Number.isInteger(v));      // counts: one bar per value (or per even step)
  const size = hi > lo ? (whole ? Math.max(1, Math.ceil((hi - lo) / 40)) : (hi - lo) / 30) : 1;
  const muted = cssVar("--muted"), grid = cssVar("--line");
  Plotly.react(id, [{ type: "histogram", x: shown, histnorm: "probability", autobinx: false,
      xbins: { start: lo, end: hi + size, size }, marker: { color, opacity: 0.85 },
      hovertemplate: "%{x}: %{y:.1%}<extra></extra>" }], {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 11 },
    margin: { l: 44, r: 10, t: 26, b: 38 }, bargap: 0.05, showlegend: false,
    xaxis: { title: { text: xTitle, font: { size: 11, color: muted } }, gridcolor: grid, zeroline: false, color: muted,
             range: [lo - size, hi + 2 * size], tickformat: xfmt || "" },
    yaxis: { title: { text: "probability", font: { size: 11, color: muted } }, gridcolor: grid, zeroline: false, color: muted, tickformat: ".0%" },
    shapes: lines.map((l) => l.shape), annotations: lines.map((l) => l.note), uirevision: id,
  }, { displaylogo: false, responsive: true });
}

function flLine(x, color, label, row) {
  const l = vline(x, color, "dash", label);
  l.note.y = 1 - row * 0.1;
  l.note.xanchor = "left";
  l.note.xshift = 4;
  return l;
}

function renderFleetLive() {
  const n = fl.revealed, obs = state.observed || {};
  const ck = checkpointAt(n);
  const pct = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
  const int = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-GB"));
  $("fl-progress").textContent = `· ${int(n)} / ${int(fl.total)} simulated days` +
    (fl.done && fl.revealed < (fl.series.share || []).length ? " (replaying a finished run)" : "");
  if (!ck) return;
  $("fl-avail").textContent = pct(ck.fleet_availability_mean);
  $("fl-ontime").textContent = pct(ck.p_delivery_within_target);
  $("fl-fuel").textContent = `€${int(ck.fuel_cost_mean)}`;
  $("fl-fuel-s").textContent = `${int(ck.fuel_l_mean)} L per day`;
  $("fl-break").textContent = pct(ck.p_breakdowns_over_alert);
  $("fl-break-s").textContent = `P(> ${ck.breakdown_alert} breakdowns per day)`;
  $("fl-sla").textContent = pct(ck.p_meet_sla);
  $("fl-sla-s").textContent = obs.stat_label ? `${obs.stat_label} of deliveries on time` : "";

  const slice = (k) => ({ all: fl.series[k] || [], shown: (fl.series[k] || []).slice(0, n) });
  const warm = cssVar("--surrogate"), bad = cssVar("--bad"), good = cssVar("--good"), blue = cssVar("--observed");
  flHist("fl-vehicles", slice("vehicles_required"), blue, "number of vehicles", [
    flLine(ck.vehicles_required_mean, warm, `expected ${int(ck.vehicles_required_mean)}`, 0),
    flLine(ck.vehicles_required_p95, bad, `P95 ${int(ck.vehicles_required_p95)}`, 1),
    ...(obs.headline != null ? [flLine(obs.headline, good, `fleet ${obs.headline}`, 2)] : []),
  ]);
  flHist("fl-maint", slice("maint_cost"), good, "daily maintenance cost (€)", [
    flLine(ck.maint_cost_mean, warm, `expected €${int(ck.maint_cost_mean)}`, 0),
    flLine(ck.maint_cost_p95, bad, `P95 €${int(ck.maint_cost_p95)}`, 1),
  ]);
  flHist("fl-share", slice("share"), warm, "on-time share of the day", [
    ...(obs.stat != null ? [flLine(obs.stat, blue, obs.stat_label || "SLA", 0)] : []),
  ], ".0%");
  const on = ck.on_time_share_mean;
  Plotly.react("fl-outcome", [{ type: "pie", hole: 0.55, sort: false, labels: ["On time", "Delayed"],
      values: [on, 1 - on], marker: { colors: [good, bad] }, textinfo: "percent",
      textfont: { color: "#fff", size: 13 }, hovertemplate: "%{label}: %{percent}<extra></extra>" }], {
    paper_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 11 },
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: true, legend: { orientation: "h", y: -0.02 },
  }, { displaylogo: false, responsive: true });
}

function setMode(test) {
  state.test = test;
  const fleet = test === "fleet";
  $("main-grid").classList.toggle("fleet-mode", fleet);
  $("fleet-live").hidden = !fleet;
  $("cloud-title").textContent = fleet ? "Fleet records by TDA regime" : "Data vs. random surrogate";
  $("cloud-hint").textContent = fleet
    ? "Every vehicle-day record, coloured by the regime TDA assigned it. Orange: the vehicles drawn for the day being simulated."
    : "Observed points (blue) and the structureless surrogate the current simulation ran on (orange).";
  $("hist-title").textContent = fleet ? "On-time share per simulated day" : "Null distribution";
  $("table-title").textContent = fleet ? "Fleet simulation" : "Loops";
  $("loops-wrap").hidden = fleet;
  $("fleet-summary").hidden = !fleet;
}

function renderFleetSummary(fleet) {
  const box = $("fleet-summary");
  box.replaceChildren();
  if (!fleet) return;
  const k = fleet.summary.kpi, c = fleet.config;
  const pct = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
  const num = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-GB"));
  [["P(meet SLA)", pct(k.p_meet_sla)], ["Delivered within " + c.delivery_target_h + " h", pct(k.p_delivery_within_target)],
   ["Vehicles needed (P95)", `${num(k.vehicles_required_mean)} (${num(k.vehicles_required_p95)})`],
   ["Fleet availability", pct(k.fleet_availability_mean)], ["Fuel per day", `${num(k.fuel_l_mean)} L`],
   ["Breakdown risk", `${pct(k.p_breakdowns_over_alert)} (> ${k.breakdown_alert})`],
   ["Maintenance per day", `€${num(k.maint_cost_mean)}`], ["Simulated days", num(fleet.n_completed)]]
    .forEach(([label, value]) => {
      const item = document.createElement("div");
      item.className = "item";
      const a = document.createElement("span"); a.className = "k"; a.textContent = label;
      const b = document.createElement("span"); b.className = "v"; b.textContent = value;
      item.append(a, b);
      box.appendChild(item);
    });
  $("verdict").textContent = `· ${pct(k.p_meet_sla)} of days meet the ${Math.round(c.sla_on_time * 100)}% on-time SLA`;
}

function resetView() {
  resetFleet();
  Object.assign(state, { null: [], observed: null, frame: null, running: null, status: null, plotReady: false });
  $("report-btn").hidden = true;
  $("fleet-summary").replaceChildren();
  Plotly.purge("plot3d");
  Plotly.purge("plothist");
  renderTable(null, "Results appear when the job finishes.");
  $("verdict").textContent = "";
  ["st-n", "st-p", "st-band", "st-top"].forEach((id) => { $(id).textContent = "–"; $(id).className = "value"; });
  $("progress-bar").style.width = "0";
}

async function poll(generation) {
  if (generation !== state.generation) return;
  try {
    const view = await api(`/v1/jobs/${state.jobId}/live?since=${state.null.length}`);
    if (generation !== state.generation) return;
    state.status = view.status;
    const test = view.live.loops ? "loops" : view.live.fleet ? "fleet" : null;
    if (test && state.test !== test) setMode(test);
    const feed = test ? view.live[test] : null;
    if (!feed) {
      renderStats(view);
      $("status-line").textContent += " · this job has nothing to show live.";
    } else {
      if (feed.null_from !== state.null.length) {        // out of sync → restart from 0
        state.null = [];
        state.observed = null;
        schedule(generation);
        return;
      }
      if (feed.observed) state.observed = feed.observed;
      if (test === "fleet") {
        const p = (view.progress && view.progress.fleet) || {};
        flReceive(feed, p.total);
      }
      state.null.push(...feed.null);
      state.frame = feed.frame;
      state.running = feed.running;
      render3d();
      renderHist();
      renderStats(view);
    }
    if (ACTIVE.includes(view.status)) {
      schedule(generation);
    } else {
      await showResult(view);
      refreshJobs(state.jobId);
    }
    showError("");
  } catch (err) {
    showError(`Live update failed: ${err.message}`);
    schedule(generation, 2000);
  }
}

function schedule(generation, delay = POLL_MS) {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => poll(generation), delay);
}

async function showResult(view) {
  if (view.status === "cancelled") {
    renderTable(null);
    $("verdict").textContent = "· cancelled";
    return;
  }
  const result = await api(`/v1/jobs/${state.jobId}/result`);
  if (state.test === "fleet") {
    renderFleetSummary(result.fleet);
    fl.done = true;
    if (!fl.timer) finishFleet();
  } else renderTable(result.loops);
  // the job is done: its report (HTML view + PDF + Excel) can be generated
  const btn = $("report-btn");
  btn.href = `/report?job=${encodeURIComponent(state.jobId)}`;
  btn.hidden = false;
}

function selectJob(jobId) {
  clearTimeout(state.timer);
  state.generation += 1;
  state.jobId = jobId || null;
  resetView();
  if (state.jobId) poll(state.generation);
  else $("status-line").textContent = "Pick a job or run a synthetic simulation.";
}

// ── Jobs list, run, cancel ──────────────────────────────────────────────────
async function refreshJobs(selectId) {
  try {
    const jobs = await api("/v1/jobs");
    const sel = $("job-select");
    sel.replaceChildren();
    if (!jobs.length) sel.add(new Option("— no jobs yet —", ""));
    for (const j of jobs) {
      const time = new Date(j.created * 1000).toLocaleTimeString();
      sel.add(new Option(`${time} · ${j.dataset_id} · ${j.status}`, j.job_id));
    }
    const target = selectId || state.jobId;
    if (target && jobs.some((j) => j.job_id === target)) sel.value = target;
    return jobs;
  } catch (err) {
    showError(`Could not load jobs: ${err.message}`);
    return [];
  }
}

async function runSynthetic() {
  const shape = $("syn-shape").value;
  const request = {
    contract_version: "1",
    dataset_id: `synthetic-${shape}`,
    settings: { n_sims: Number($("syn-nsims").value), seed: 42, alpha: 0.05, max_seconds: 600 },
    loops: { X: synthetic(shape), sample_n: Number($("syn-sample").value), null: "gaussian" },
  };
  $("run").disabled = true;
  try {
    const job = await api("/v1/jobs", { method: "POST", body: JSON.stringify(request) });
    await refreshJobs(job.job_id);
    selectJob(job.job_id);
    showError("");
  } catch (err) {
    showError(`Could not start the simulation: ${err.message}`);
  } finally {
    $("run").disabled = false;
  }
}

async function cancelJob() {
  if (!state.jobId) return;
  try {
    await api(`/v1/jobs/${state.jobId}`, { method: "DELETE" });
  } catch (err) {
    showError(`Cancel failed: ${err.message}`);
  }
}

// ── Camera rotation ─────────────────────────────────────────────────────────
let lastTurn = 0;
function rotate(ts) {
  if ($("rotate").checked && state.plotReady && ts - lastTurn > 50) {
    lastTurn = ts;
    state.eye.angle += 0.012;
    Plotly.relayout("plot3d", { "scene.camera.eye": eyeXYZ() });
  }
  requestAnimationFrame(rotate);
}

// ── Start ───────────────────────────────────────────────────────────────────
async function init() {
  $("job-select").addEventListener("change", (e) => selectJob(e.target.value));
  $("refresh").addEventListener("click", () => refreshJobs());
  $("run").addEventListener("click", runSynthetic);
  $("cancel").addEventListener("click", cancelJob);
  $("fl-replay").addEventListener("click", () => { fl.revealed = 0; $("fl-replay").disabled = true; startReveal(); });
  requestAnimationFrame(rotate);

  try {
    const h = await api("/health");
    $("health").textContent = `v${h.version} · contract ${h.contract_version}`;
    $("health-dot").className = "dot ok";
  } catch (err) {
    $("health").textContent = "service unreachable";
    $("health-dot").className = "dot down";
    showError(`Health check failed: ${err.message}`);
  }

  const jobs = await refreshJobs();
  // ?job=<id> (e.g. from CortXplorer's "Open live 3D view") selects that job
  const wanted = new URLSearchParams(location.search).get("job");
  const linked = wanted && jobs.find((j) => j.job_id === wanted);
  if (wanted && !linked) showError(`Job ${wanted} was not found (finished jobs expire after the configured TTL).`);
  const active = jobs.find((j) => ACTIVE.includes(j.status));
  const first = linked || active || jobs[0];
  if (first) {
    $("job-select").value = first.job_id;
    selectJob(first.job_id);
  }
}

init();
