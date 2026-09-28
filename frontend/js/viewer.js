// Monte Carlo live viewer. Polls GET /v1/jobs/{id}/live and draws what the
// service sends: the observed cloud, the surrogate of the latest simulation,
// the growing null distribution and the server-computed running p-value and
// noise band. Statistics are never computed here.

const POLL_MS = 350;
const ACTIVE = ["queued", "running"];

const $ = (id) => document.getElementById(id);
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const state = {
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
  const axis = { showbackground: false, gridcolor: grid, zerolinecolor: grid, color: ink, title: { text: "" } };
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: cssVar("--ink"), size: 12 },
    margin: { l: 0, r: 0, t: 8, b: 0 },
    showlegend: true,
    legend: { orientation: "h", x: 0, y: 1.02, bgcolor: "rgba(0,0,0,0)" },
    scene: { xaxis: axis, yaxis: axis, zaxis: axis, aspectmode: "data", camera: { eye: eyeXYZ() } },
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

function render3d() {
  const traces = [];
  if (state.observed) traces.push(cloudTrace(state.observed.points, "Observed data", cssVar("--observed"), 0.85));
  if (state.frame) {
    traces.push(cloudTrace(state.frame.points, `Surrogate · simulation ${state.frame.index + 1}`,
      cssVar("--surrogate"), 0.55));
  }
  if (!traces.length) return;
  Plotly.react("plot3d", traces, baseLayout(), { displaylogo: false, responsive: true });
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
  if (obs && obs.stat !== null) lines.push(vline(obs.stat, cssVar("--observed"), "solid", "observed"));
  if (state.running && state.running.noise_band !== null) {
    lines.push(vline(state.running.noise_band, cssVar("--band"), "dash", `${Math.round((1 - obs.alpha) * 100)}% band`));
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
  const prog = (view.progress && view.progress.loops) || { done: 0, total: 0 };
  $("st-n").textContent = prog.total ? `${prog.done} / ${prog.total}` : "–";
  $("progress-bar").style.width = prog.total ? `${(100 * prog.done) / prog.total}%` : "0";

  const r = state.running || {};
  const alpha = state.observed ? state.observed.alpha : 0.05;
  const pEl = $("st-p");
  pEl.textContent = fmtP(r.p_value);
  const finished = !ACTIVE.includes(view.status);
  pEl.className = "value" + (!finished || r.p_value === null || r.p_value === undefined ? "" : r.p_value <= alpha ? " good" : " bad");
  $("st-band").textContent = fmt(r.noise_band);
  $("st-top").textContent = fmt(state.observed && state.observed.stat);
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
function resetView() {
  Object.assign(state, { null: [], observed: null, frame: null, running: null, status: null, plotReady: false });
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
    const feed = view.live.loops;
    if (!feed) {
      renderStats(view);
      $("status-line").textContent += " · the live 3D view shows the loop test; this job has none.";
    } else {
      if (feed.null_from !== state.null.length) {        // out of sync → restart from 0
        state.null = [];
        state.observed = null;
        schedule(generation);
        return;
      }
      if (feed.observed) state.observed = feed.observed;
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
  renderTable(result.loops);
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
