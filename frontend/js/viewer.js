// Monte Carlo live viewer (fleet). Polls GET /v1/jobs/{id}/live for a fleet job
// sent by CortXplorer and fills the sample dashboard day by day: KPI tiles and
// the vehicle requirement / delivery outcome / maintenance cost / on-time share
// distributions, plus the fleet records in 3D by TDA regime.
//
// Every KPI shown is computed by the service (running checkpoints). The service
// finishes 100,000 days in seconds, so the viewer plays the simulated days back
// over a chosen duration (15 / 30 / 60 s) so people can watch the distributions
// build up; it always shows how many days have been revealed.

const POLL_MS = 350;
const ACTIVE = ["queued", "running"];
const FRAME_MS = 100;
// Categorical colours for TDA regimes (fixed order); grey for noise
const REGIME_COLORS = ["#2a78d6", "#1baf7a", "#e87ba4", "#4a3aa7", "#008300", "#eda100", "#e34948"];

const $ = (id) => document.getElementById(id);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const pct = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
const int = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-GB"));

const state = {
  jobId: null, status: null, generation: 0, timer: null, received: 0,
  observed: null, frame: null, plotReady: false,
  eye: { angle: Math.PI / 4, r: 2.1, z: 1.1 },
};
const fl = { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false, paused: false };
const eyeXYZ = () => ({ x: state.eye.r * Math.cos(state.eye.angle), y: state.eye.r * Math.sin(state.eye.angle), z: state.eye.z });

// ── API ─────────────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok || !body || body.status === "error") throw new Error((body && body.message) || `${res.status} ${res.statusText}`);
  return body.data;
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

// ── 3D: records by TDA regime ───────────────────────────────────────────────
function cloudTrace(points, name, color, opacity, size) {
  return {
    type: "scatter3d", mode: "markers", name,
    x: points.map((p) => p[0]), y: points.map((p) => p[1]), z: points.map((p) => p[2]),
    marker: { size, color, opacity }, hoverinfo: "skip",
  };
}

function render3d() {
  const obs = state.observed;
  if (!obs || !obs.groups) return;
  const ids = [...new Set(obs.groups)].sort((a, b) => a - b);
  const traces = ids.map((g, i) => cloudTrace(obs.points.filter((_, k) => obs.groups[k] === g),
    (obs.group_labels && obs.group_labels[String(g)]) || `Regime ${g}`,
    g === -1 ? "#8a8f98" : REGIME_COLORS[i % REGIME_COLORS.length], 0.35, 2.4));
  if (state.frame && state.frame.highlight) {
    traces.push(cloudTrace(state.frame.highlight.map((k) => obs.points[k]),
      `Vehicles of a simulated day (${state.frame.day})`, cssVar("--surrogate"), 0.95, 5));
  }
  const muted = cssVar("--muted"), grid = cssVar("--line");
  const axis = (i) => ({ showbackground: false, gridcolor: grid, zerolinecolor: grid, color: muted,
                         title: { text: (obs.axis_titles || [])[i] || "" } });
  Plotly.react("plot3d", traces, {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 12 },
    margin: { l: 0, r: 0, t: 8, b: 70 }, showlegend: true,
    legend: { orientation: "h", x: 0, y: 0, yanchor: "top", bgcolor: "rgba(0,0,0,0)", font: { size: 11 } },
    scene: { xaxis: axis(0), yaxis: axis(1), zaxis: axis(2), aspectmode: "cube", camera: { eye: eyeXYZ() } },
    uirevision: "keep",
  }, { displaylogo: false, responsive: true });
  state.plotReady = true;
}

// ── dashboard ───────────────────────────────────────────────────────────────
function vline(x, color, label, row) {
  return {
    shape: { type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1, line: { color, width: 2.2, dash: "dash" } },
    note: { x, xref: "x", yref: "paper", y: 1 - row * 0.1, yanchor: "bottom", xanchor: "left", xshift: 4,
            text: label, showarrow: false, font: { size: 11, color } },
  };
}

function histogram(id, key, color, xTitle, lines, xfmt) {
  const all = fl.series[key] || [];
  if (!all.length) return;
  let lo = Infinity, hi = -Infinity, whole = true;
  for (const v of all) { if (v < lo) lo = v; if (v > hi) hi = v; if (whole && !Number.isInteger(v)) whole = false; }
  const size = hi > lo ? (whole ? Math.max(1, Math.ceil((hi - lo) / 40)) : (hi - lo) / 30) : 1;
  const muted = cssVar("--muted"), grid = cssVar("--line");
  Plotly.react(id, [{ type: "histogram", x: all.slice(0, fl.revealed), histnorm: "probability", autobinx: false,
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

function checkpointAt(n) {
  let best = null;
  for (const c of fl.checkpoints) if (c.n <= n) best = c;
  return best || fl.checkpoints[0] || null;
}

function renderDashboard() {
  const n = fl.revealed, obs = state.observed || {};
  const ck = checkpointAt(n);
  const playing = fl.done && n < (fl.series.share || []).length;
  $("fl-progress").textContent = `· ${int(n)} / ${int(fl.total)} simulated days` +
    (playing ? " · playing back the finished run" : "");
  $("progress-bar").style.width = fl.total ? `${(100 * n) / fl.total}%` : "0";
  if (!ck) return;
  $("fl-avail").textContent = pct(ck.fleet_availability_mean);
  $("fl-ontime").textContent = pct(ck.p_delivery_within_target);
  $("fl-fuel").textContent = `€${int(ck.fuel_cost_mean)}`;
  $("fl-fuel-s").textContent = `${int(ck.fuel_l_mean)} L per day`;
  $("fl-break").textContent = pct(ck.p_breakdowns_over_alert);
  $("fl-break-s").textContent = `P(> ${ck.breakdown_alert} breakdowns per day)`;
  $("fl-sla").textContent = pct(ck.p_meet_sla);
  $("fl-sla-s").textContent = obs.stat_label ? `${obs.stat_label} of deliveries on time` : "";

  const warm = cssVar("--surrogate"), bad = cssVar("--bad"), good = cssVar("--good"), blue = cssVar("--observed");
  histogram("fl-vehicles", "vehicles_required", blue, "number of vehicles", [
    vline(ck.vehicles_required_mean, warm, `expected ${int(ck.vehicles_required_mean)}`, 0),
    vline(ck.vehicles_required_p95, bad, `P95 ${int(ck.vehicles_required_p95)}`, 1),
    ...(obs.headline != null ? [vline(obs.headline, good, `fleet ${obs.headline}`, 2)] : []),
  ]);
  histogram("fl-maint", "maint_cost", good, "daily maintenance cost (€)", [
    vline(ck.maint_cost_mean, warm, `expected €${int(ck.maint_cost_mean)}`, 0),
    vline(ck.maint_cost_p95, bad, `P95 €${int(ck.maint_cost_p95)}`, 1),
  ]);
  histogram("fl-share", "share", warm, "on-time share of the day",
    obs.stat != null ? [vline(obs.stat, blue, obs.stat_label || "SLA", 0)] : [], ".0%");
  const on = ck.on_time_share_mean;
  Plotly.react("fl-outcome", [{ type: "pie", hole: 0.55, sort: false, labels: ["On time", "Delayed"],
      values: [on, 1 - on], marker: { colors: [good, bad] }, textinfo: "percent",
      textfont: { color: "#fff", size: 13 }, hovertemplate: "%{label}: %{percent}<extra></extra>" }], {
    paper_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 11 },
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: true, legend: { orientation: "h", y: -0.02 },
  }, { displaylogo: false, responsive: true });
}

// ── playback: reveal the simulated days over the chosen duration ───────────
function playbackSeconds() {
  return Number($("speed").value) || 30;
}

function startReveal() {
  clearInterval(fl.timer);
  fl.paused = false;
  $("fl-pause").disabled = false;
  $("fl-pause").textContent = "Pause";
  fl.timer = setInterval(() => {
    if (fl.paused) return;
    const received = (fl.series.share || []).length;
    const step = Math.max(1, Math.ceil((fl.total || received || 1) / (playbackSeconds() * 1000 / FRAME_MS)));
    fl.revealed = Math.min(received, fl.revealed + step);
    renderDashboard();
    if (fl.revealed >= received && (fl.done || !ACTIVE.includes(state.status))) {
      clearInterval(fl.timer);
      fl.timer = null;
      $("fl-pause").disabled = true;
      if (fl.done) finish();
    }
  }, FRAME_MS);
}

function finish() {
  $("fl-replay").disabled = false;
  const btn = $("fl-report");
  btn.href = `/report?job=${encodeURIComponent(state.jobId)}`;
  btn.hidden = false;
}

// ── polling ─────────────────────────────────────────────────────────────────
function resetView() {
  clearInterval(fl.timer);
  Object.assign(fl, { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false, paused: false });
  Object.assign(state, { received: 0, observed: null, frame: null, status: null, plotReady: false });
  ["fl-vehicles", "fl-outcome", "fl-maint", "fl-share", "plot3d"].forEach((id) => Plotly.purge(id));
  ["fl-avail", "fl-ontime", "fl-fuel", "fl-break", "fl-sla"].forEach((id) => { $(id).textContent = "–"; });
  $("fl-report").hidden = true;
  $("fl-replay").disabled = true;
  $("fl-pause").disabled = true;
  $("progress-bar").style.width = "0";
  $("fl-progress").textContent = "–";
}

async function poll(generation) {
  if (generation !== state.generation) return;
  try {
    const view = await api(`/v1/jobs/${state.jobId}/live?since=${state.received}`);
    if (generation !== state.generation) return;
    state.status = view.status;
    const feed = view.live.fleet;
    const p = (view.progress && view.progress.fleet) || { done: 0, total: 0 };
    const errs = Object.entries(view.errors || {}).map(([t, e]) => `${t}: ${e}`).join(" · ");
    $("status-line").textContent = `simulation ${view.status} · ${int(p.done)} / ${int(p.total)} days computed in ${view.elapsed_s.toFixed(1)} s · ${view.dataset_id}` + (errs ? ` · ${errs}` : "");
    $("cancel").disabled = !ACTIVE.includes(view.status);
    if (!feed) {
      showError("This job is not a fleet simulation, so there is nothing to show here.");
      return;
    }
    if (feed.null_from !== state.received) {                  // out of sync → restart from 0
      resetView();
      schedule(generation);
      return;
    }
    if (feed.observed) state.observed = feed.observed;
    for (const [k, v] of Object.entries(feed.series || {})) (fl.series[k] = fl.series[k] || []).push(...v);
    (fl.series.share = fl.series.share || []).push(...feed.null);
    state.received += feed.null.length;
    fl.checkpoints = feed.checkpoints || fl.checkpoints;
    fl.total = p.total || fl.total;
    state.frame = feed.frame;
    render3d();
    if (ACTIVE.includes(view.status)) {
      if (!fl.timer && state.received) startReveal();
      schedule(generation);
    } else {
      fl.done = view.status === "done";
      if (view.status === "cancelled") $("fl-progress").textContent += " · cancelled";
      if (!fl.timer && fl.revealed < state.received) startReveal();
      else if (fl.done && !fl.timer) finish();
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

function selectJob(jobId) {
  clearTimeout(state.timer);
  state.generation += 1;
  state.jobId = jobId || null;
  resetView();
  $("live").hidden = !state.jobId;
  const url = new URL(location.href);
  if (state.jobId) url.searchParams.set("job", state.jobId); else url.searchParams.delete("job");
  history.replaceState(null, "", url);
  if (state.jobId) poll(state.generation);
}

async function refreshJobs(selectId) {
  try {
    const jobs = (await api("/v1/jobs")).filter((j) => j.tests.includes("fleet"));
    const sel = $("job-select");
    sel.replaceChildren();
    if (!jobs.length) sel.add(new Option("— no fleet jobs yet —", ""));
    for (const j of jobs) {
      const time = new Date(j.created * 1000).toLocaleTimeString();
      sel.add(new Option(`${time} · ${j.dataset_id} · ${j.status}`, j.job_id));
    }
    const target = selectId || state.jobId;
    if (target && jobs.some((j) => j.job_id === target)) sel.value = target;
    $("empty").hidden = jobs.length > 0;
    return jobs;
  } catch (err) {
    showError(`Could not load jobs: ${err.message}`);
    return [];
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

// ── camera rotation ─────────────────────────────────────────────────────────
let lastTurn = 0;
function rotate(ts) {
  if ($("rotate").checked && state.plotReady && ts - lastTurn > 50) {
    lastTurn = ts;
    state.eye.angle += 0.012;
    Plotly.relayout("plot3d", { "scene.camera.eye": eyeXYZ() });
  }
  requestAnimationFrame(rotate);
}

// ── start ───────────────────────────────────────────────────────────────────
async function init() {
  $("job-select").addEventListener("change", (e) => selectJob(e.target.value));
  $("refresh").addEventListener("click", () => refreshJobs());
  $("cancel").addEventListener("click", cancelJob);
  $("fl-replay").addEventListener("click", () => {
    fl.revealed = 0;
    $("fl-replay").disabled = true;
    $("fl-report").hidden = true;
    startReveal();
  });
  $("fl-pause").addEventListener("click", () => {
    fl.paused = !fl.paused;
    $("fl-pause").textContent = fl.paused ? "Resume" : "Pause";
  });
  try {
    const saved = localStorage.getItem("mc-speed");
    if (saved) $("speed").value = saved;
  } catch { /* storage unavailable */ }
  $("speed").addEventListener("change", (e) => { try { localStorage.setItem("mc-speed", e.target.value); } catch { /* ignore */ } });
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
  const wanted = new URLSearchParams(location.search).get("job");
  const linked = wanted && jobs.find((j) => j.job_id === wanted);
  if (wanted && !linked) showError(`Fleet job ${wanted} was not found (finished jobs expire after the configured TTL).`);
  const first = linked || jobs.find((j) => ACTIVE.includes(j.status)) || jobs[0];
  if (first) {
    $("job-select").value = first.job_id;
    selectJob(first.job_id);
  }
}

init();
