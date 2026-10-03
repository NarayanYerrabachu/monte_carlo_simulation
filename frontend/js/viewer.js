// Monte Carlo live viewer. Polls GET /v1/jobs/{id}/live for a job sent by
// CortXplorer and fills the dashboard period by period: KPI tiles, distribution
// charts (2D / 3D) and the TDA Mapper graph of the records.
//
// Two kinds of job share the page:
//   fleet     the fleet dashboard (vehicle requirement / delivery outcome /
//             maintenance cost / on-time share), fixed in index.html
//   scenario  any other table: the job describes its own tiles and charts
//             (observed.dashboard) and the Mapper colouring (graph.view), and
//             the page builds them — nothing here knows the dataset's columns.
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

// adaptive number: 12,345 · 123.4 · 12.35 · 0.123
const num = (v) => {
  if (v == null || !Number.isFinite(v)) return "–";
  const a = Math.abs(v), d = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : 3;
  return v.toLocaleString("en-GB", { maximumFractionDigits: d });
};
// value in the format a job asked for ("pct" | "int" | "num"), with its unit
function fmtVal(v, fmt, unit) {
  if (v == null) return "–";
  const s = fmt === "pct" ? pct(v) : fmt === "int" ? int(v) : num(v);
  if (!unit || fmt === "pct") return s;
  return unit === "€" || unit === "$" || unit === "£" ? `${unit}${s}` : `${s} ${unit}`;
}
const TONE_VAR = { blue: "--observed", good: "--good", warm: "--surrogate", bad: "--bad" };
const TONE_INK = { blue: "accent-ink", good: "good-ink", warm: "warm-ink", bad: "bad-ink" };
const tone = (t) => cssVar(TONE_VAR[t] || "--observed");
// scenario jobs: the dashboard the job described (null for fleet jobs)
const gx = { dash: null };
const unitWord = () => (gx.dash ? gx.dash.unit : "days");

const state = {
  jobId: null, status: null, generation: 0, timer: null, received: 0,
  observed: null, frame: null, plotReady: false,
  eye: { angle: Math.PI / 4, r: Math.hypot(1.6, 1.6), z: 0.9 },   // CortXplorer's TDA Mapper camera
  tdaView: "mapper",                                              // "mapper" | "galaxy" (same graph, two looks)
};
const fl = { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false, paused: false };
// every chart: scroll / pinch zoom and the zoom, pan and reset tools
const PLOT_CONFIG = { displaylogo: false, responsive: true, scrollZoom: true };
// The user's zoom per chart ({x, y} axis ranges or {camera}), re-applied on every redraw:
// the dashboard redraws 10× a second during playback and would otherwise reset it.
const userView = {};

function rememberView(id) {
  const el = $(id);
  if (!el || el._mcView || !el.on) return;
  el._mcView = true;
  el.on("plotly_relayout", (ev) => {
    if (!ev || el._mcApplying) return;
    const v = (userView[id] = userView[id] || {});
    if (ev["xaxis.autorange"] || ev.autosize) { delete userView[id]; return; }
    if ("xaxis.range[0]" in ev) v.x = [ev["xaxis.range[0]"], ev["xaxis.range[1]"]];
    if (ev["xaxis.range"]) v.x = ev["xaxis.range"];
    if ("yaxis.range[0]" in ev) v.y = [ev["yaxis.range[0]"], ev["yaxis.range[1]"]];
    if (ev["yaxis.range"]) v.y = ev["yaxis.range"];
    const cam = ev["scene.camera"] || (ev["scene.camera.eye"] && { eye: ev["scene.camera.eye"] });
    if (cam && cam.eye) v.camera = { ...(v.camera || {}), ...cam };
  });
}

// Plotly.react that keeps the user's zoom on this chart
function reactKeepingView(id, traces, layout) {
  const v = userView[id];
  if (v) {
    if (v.x && layout.xaxis) layout.xaxis = { ...layout.xaxis, range: v.x, autorange: false };
    if (v.y && layout.yaxis) layout.yaxis = { ...layout.yaxis, range: v.y, autorange: false };
    if (v.camera && layout.scene) layout.scene = { ...layout.scene, camera: { ...layout.scene.camera, ...v.camera } };
  }
  const el = $(id);
  el._mcApplying = true;
  const done = Plotly.react(el, traces, layout, PLOT_CONFIG);
  el._mcApplying = false;
  rememberView(id);
  return done;
}
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

// TDA Mapper graph in 3D (drawn by mapper_view.js, shared with the report page).
// Orange rings mark the groups holding the records of the simulated period shown.
function renderMapper3d(frame) {
  const g = state.observed.graph;
  const gv = MapperView.graphView(g);
  const shown = MapperView.labelled(g, gv);
  MapperView.keyTable($("mapper-table"), shown, gv, $("mapper-note"));
  $("mapper-key").hidden = !shown.length;
  $("plot3d").parentElement.classList.toggle("has-key", shown.length > 0);
  const view = MapperView.VIEWS[state.tdaView];
  Plotly.react("plot3d", view.traces(g, gv, shown, frame), view.layout(g, gv, eyeXYZ()), PLOT_CONFIG);
  state.plotReady = true;
}

// Mapper ↔ Galaxy: the same graph with another look; the camera goes to that view's default.
function setTdaView(name) {
  if (!MapperView.VIEWS[name]) return;
  state.tdaView = name;
  document.querySelectorAll(".seg button[data-tda]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tda === name)));
  const e = MapperView.VIEWS[name].eye;
  Object.assign(state.eye, { r: Math.hypot(e.x, e.y), z: e.z });
  try { localStorage.setItem("mc-tda-view", name); } catch { /* storage unavailable */ }
  if (state.observed && state.observed.graph) {
    Plotly.purge("plot3d");                                  // new scene (background, aspect): start clean
    state.plotReady = false;
    state.shownFrame = null;
    render3d(frameAt(fl.revealed));
  }
}

function frameAt(n) {
  let best = null;
  for (const f of state.frames || []) if ((f.n || f.index + 1) <= n) best = f;
  return best || state.frame;
}

function render3d(frame = state.frame) {
  const obs = state.observed;
  if (obs && obs.graph) {
    if (state.shownFrame === frame && state.plotReady) return;
    state.shownFrame = frame;
    const g = obs.graph, gv = MapperView.graphView(g);
    $("cloud-title").textContent = state.tdaView === "galaxy" ? gv.title.replace("TDA Mapper", "TDA Galaxy") : gv.title;
    $("cloud-hint").textContent = `${MapperView.hint(g, gv, state.tdaView)} ${MapperView.VIEWS[state.tdaView].ring}: ${gv.ring}.`;
    renderMapper3d(frame);
    return;
  }
  if (!obs || !obs.groups) return;
  const ids = [...new Set(obs.groups)].sort((a, b) => a - b);
  const traces = ids.map((g, i) => cloudTrace(obs.points.filter((_, k) => obs.groups[k] === g),
    (obs.group_labels && obs.group_labels[String(g)]) || `Regime ${g}`,
    g === -1 ? "#8a8f98" : REGIME_COLORS[i % REGIME_COLORS.length], 0.35, 2.4));
  if (state.frame && state.frame.highlight) {
    traces.push(cloudTrace(state.frame.highlight.map((k) => obs.points[k]),
      `${gx.dash ? "Records" : "Vehicles"} of a simulated ${gx.dash ? gx.dash.period_label : "day"} (${state.frame.day})`, cssVar("--surrogate"), 0.95, 5));
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
  }, PLOT_CONFIG);
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
  reactKeepingView(id, [{ type: "histogram", x: all.slice(0, fl.revealed), histnorm: "probability", autobinx: false,
      xbins: { start: lo, end: hi + size, size }, marker: { color, opacity: 0.85 },
      hovertemplate: "%{x}: %{y:.1%}<extra></extra>" }], {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 11 },
    margin: { l: 44, r: 10, t: 26, b: 38 }, bargap: 0.05, showlegend: false,
    xaxis: { title: { text: xTitle, font: { size: 11, color: muted } }, gridcolor: grid, zeroline: false, color: muted,
             range: [lo - size, hi + 2 * size], tickformat: xfmt || "" },
    yaxis: { title: { text: "probability", font: { size: 11, color: muted } }, gridcolor: grid, zeroline: false, color: muted, tickformat: ".0%" },
    shapes: lines.map((l) => l.shape), annotations: lines.map((l) => l.note), uirevision: id,
  });
}

// ── 3D views ────────────────────────────────────────────────────────────────
// Waterfall: the distribution after every 10 % of the simulated days (depth), so
// it can be seen growing and settling. Joint: vehicles required × on-time share.
// Bin counts are updated incrementally as days are revealed (no re-counting).
const view3d = { vehicles: false, outcome: false, maint: false, share: false };
const PLOT_ID = { vehicles: "fl-vehicles", outcome: "fl-outcome", maint: "fl-maint", share: "fl-share" };
const inc = {};
let frameNo = 0;

function binSpec(key, target = 30) {
  const all = fl.series[key] || [];
  if (!all.length) return null;
  let lo = Infinity, hi = -Infinity, whole = true;
  for (const v of all) { if (v < lo) lo = v; if (v > hi) hi = v; if (whole && !Number.isInteger(v)) whole = false; }
  const size = hi > lo ? (whole ? Math.max(1, Math.ceil((hi - lo) / (target + 10))) : (hi - lo) / target) : 1;
  return { lo, size, nb: Math.floor((hi - lo) / size) + 1 };
}

const binOf = (sp, v) => Math.min(sp.nb - 1, Math.max(0, Math.floor((v - sp.lo) / sp.size)));
const mids = (sp) => Array.from({ length: sp.nb }, (_, i) => sp.lo + (i + 0.5) * sp.size);

function countsFor(key) {
  const sp = binSpec(key);
  if (!sp) return null;
  const sig = `${sp.lo}|${sp.size}|${sp.nb}`;
  let st = inc[key];
  if (!st || st.sig !== sig || fl.revealed < st.upto) {
    st = inc[key] = { sig, sp, counts: new Float64Array(sp.nb), upto: 0, slices: [] };
  }
  const vals = fl.series[key];
  const every = Math.max(1, Math.round((fl.total || vals.length) / 10));
  for (let i = st.upto; i < fl.revealed; i++) {
    const v = vals[i];
    if (v != null && Number.isFinite(v)) st.counts[binOf(sp, v)] += 1;
    if ((i + 1) % every === 0) st.slices.push({ n: i + 1, p: Array.from(st.counts, (c) => c / (i + 1)) });
  }
  st.upto = fl.revealed;
  return st;
}

function scene(xTitle, yTitle, zTitle, xfmt, yfmt) {
  const muted = cssVar("--muted"), grid = cssVar("--line"), ink = cssVar("--ink");
  const ax = (t, fmt) => ({ title: { text: t, font: { size: 13, color: ink } }, tickfont: { size: 12, color: muted },
                            nticks: 6, gridcolor: grid, zerolinecolor: grid, showbackground: false, tickformat: fmt || "" });
  return { xaxis: ax(xTitle, xfmt), yaxis: ax(yTitle, yfmt), zaxis: ax(zTitle, ".0%"),
           camera: { eye: { x: 1.9, y: -1.9, z: 1.05 } }, aspectmode: "manual", aspectratio: { x: 1.7, y: 1.3, z: 0.65 } };
}

function surfaceLayout(sc, legend = false) {
  return { paper_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 12 },
           margin: { l: 10, r: 10, t: 10, b: 10 }, scene: sc, uirevision: "3d", showlegend: legend,
           legend: { orientation: "v", x: 1, xanchor: "right", y: 0.95, bgcolor: "rgba(0,0,0,0)", font: { size: 11 } } };
}

// "#rrggbb" → "rgba(r,g,b,a)" (Plotly fills need plain colours)
const rgba = (hex, a) => {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${a})`;
};

function waterfall(id, key, color, xTitle, xfmt) {
  const st = countsFor(key);
  if (!st || !st.upto) return;
  const rows = [...st.slices];
  if (!rows.length || rows[rows.length - 1].n !== st.upto) rows.push({ n: st.upto, p: Array.from(st.counts, (c) => c / st.upto) });
  // trim empty bins at both ends so the ridges fill the width
  const last = rows[rows.length - 1].p;
  let i0 = last.findIndex((v) => v > 0), i1 = last.length - 1 - [...last].reverse().findIndex((v) => v > 0);
  if (i0 < 0) { i0 = 0; i1 = last.length - 1; }
  const x = mids(st.sp).slice(i0, i1 + 1);
  const traces = [];
  rows.forEach((r, j) => {
    const shade = 0.25 + 0.75 * (j + 1) / rows.length;          // early slices light, final slice dark
    const z = r.p.slice(i0, i1 + 1);
    // exact fill under the curve: a triangle strip between the curve and the floor
    const vx = [], vy = [], vz = [], I = [], J = [], K = [];
    x.forEach((xv, k) => { vx.push(xv, xv); vy.push(r.n, r.n); vz.push(z[k], 0); });
    for (let k = 0; k < x.length - 1; k++) {
      const t = 2 * k, b = 2 * k + 1, t2 = 2 * k + 2, b2 = 2 * k + 3;
      I.push(t, b); J.push(b, b2); K.push(t2, t2);
    }
    traces.push({ type: "mesh3d", x: vx, y: vy, z: vz, i: I, j: J, k: K, color: color,
                  opacity: 0.12 + 0.4 * shade, flatshading: true, hoverinfo: "skip", showlegend: false });
    traces.push({
      type: "scatter3d", mode: "lines", name: `after ${int(r.n)} ${unitWord()}`,
      x, y: x.map(() => r.n), z,
      line: { color: rgba(color, Math.min(1, shade + 0.1)), width: j === rows.length - 1 ? 6 : 3 },
      hovertemplate: `%{x:,.2f} after ${int(r.n)} ${unitWord()}: %{z:.1%}<extra></extra>`,
      showlegend: j === 0 || j === rows.length - 1,
    });
  });
  reactKeepingView(id, traces, surfaceLayout(scene(xTitle, `simulated ${unitWord()}`, "probability", xfmt), true));
}

// Breakdowns × on-time share as 3D columns on a coarse grid over the central
// 99 % of days. (Not vehicles required × on-time: vehicles required ≈ fleet ÷
// on-time share by construction, so that pair only draws an identity line.)
const JOINT_CELLS = 12;

function joint(id) {
  const sv = binSpec("breakdowns", 60), ss = binSpec("share", 60);          // fine counts, regrouped below
  if (!sv || !ss) return;
  const sig = `${sv.lo}|${sv.size}|${sv.nb}|${ss.lo}|${ss.size}|${ss.nb}`;
  let st = inc.joint;
  if (!st || st.sig !== sig || fl.revealed < st.upto) {
    st = inc.joint = { sig, counts: Array.from({ length: ss.nb }, () => new Float64Array(sv.nb)), upto: 0 };
  }
  const V = fl.series.breakdowns, S = fl.series.share;
  for (let i = st.upto; i < fl.revealed; i++) {
    if (Number.isFinite(V[i]) && Number.isFinite(S[i])) st.counts[binOf(ss, S[i])][binOf(sv, V[i])] += 1;
  }
  st.upto = fl.revealed;
  if (!st.upto) return;

  // central 99 %: fine bins from the 0.5 % to the 99.5 % cumulative share, per axis
  const colSum = Array.from({ length: sv.nb }, (_, c) => st.counts.reduce((a, row) => a + row[c], 0));
  const rowSum = st.counts.map((row) => row.reduce((a, v) => a + v, 0));
  const central = (arr) => {
    const tot = arr.reduce((a, v) => a + v, 0);
    let acc = 0, lo = 0, hi = arr.length - 1;
    for (let k = 0; k < arr.length; k++) { acc += arr[k]; if (acc / tot >= 0.005) { lo = k; break; } }
    acc = 0;
    for (let k = arr.length - 1; k >= 0; k--) { acc += arr[k]; if (acc / tot >= 0.005) { hi = k; break; } }
    return [lo, hi];
  };
  const [c0, c1] = central(colSum), [r0, r1] = central(rowSum);
  const group = (lo, hi) => Math.max(1, Math.ceil((hi - lo + 1) / JOINT_CELLS));
  const gc = group(c0, c1), gr = group(r0, r1);
  const nC = Math.ceil((c1 - c0 + 1) / gc), nR = Math.ceil((r1 - r0 + 1) / gr);
  const cell = Array.from({ length: nR }, () => new Float64Array(nC));
  for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) cell[Math.floor((r - r0) / gr)][Math.floor((c - c0) / gc)] += st.counts[r][c];

  const n = st.upto, obs = state.observed || {};
  const xw = sv.size * gc, yw = ss.size * gr;               // cell width in data units
  const x0 = sv.lo + c0 * sv.size, y0 = ss.lo + r0 * ss.size;
  const vx = [], vy = [], vz = [], I = [], J = [], K = [], inten = [], txt = [];
  const box = (xa, xb, ya, yb, h, p, label) => {
    const o = vx.length;
    [[xa, ya, 0], [xb, ya, 0], [xb, yb, 0], [xa, yb, 0], [xa, ya, h], [xb, ya, h], [xb, yb, h], [xa, yb, h]]
      .forEach(([a, b, c]) => { vx.push(a); vy.push(b); vz.push(c); inten.push(p); txt.push(label); });
    [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
     [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]].forEach(([a, b, c]) => { I.push(o + a); J.push(o + b); K.push(o + c); });
  };
  let pmax = 0;
  for (let r = 0; r < nR; r++) for (let c = 0; c < nC; c++) {
    const p = cell[r][c] / n;
    if (p <= 0) continue;
    pmax = Math.max(pmax, p);
    const xa = x0 + c * xw, ya = y0 + r * yw;
    const label = `${Math.round(xa)}–${Math.round(xa + xw) - 1} breakdowns · ${(ya * 100).toFixed(1)}–${((ya + yw) * 100).toFixed(1)}% on time: ${(p * 100).toFixed(2)}% of days`;
    box(xa + 0.08 * xw, xa + 0.92 * xw, ya + 0.08 * yw, ya + 0.92 * yw, p, p, label);
  }
  const traces = [{
    type: "mesh3d", x: vx, y: vy, z: vz, i: I, j: J, k: K, intensity: inten, cmin: 0, cmax: pmax || 1,
    colorscale: [[0, "#fde7cf"], [0.35, "#f6a15a"], [0.7, cssVar("--surrogate")], [1, cssVar("--bad")]],
    flatshading: true, lighting: { ambient: 0.75, diffuse: 0.6 }, showscale: true,
    colorbar: { title: { text: "share of days", side: "right" }, tickformat: ".1%", len: 0.6, thickness: 10 },
    text: txt, hovertemplate: "%{text}<extra></extra>",
  }];
  const yEnd = y0 + nR * yw;
  if (obs.stat != null && obs.stat >= y0 && obs.stat <= yEnd) {   // the SLA on the floor
    traces.push({ type: "scatter3d", mode: "lines", x: [x0, x0 + nC * xw], y: [obs.stat, obs.stat], z: [0, 0],
                  line: { color: cssVar("--observed"), width: 7 }, hoverinfo: "skip", name: obs.stat_label || "SLA" });
  }
  const sc = scene("breakdowns in the day", "on-time share", "share of days", "", ".0%");
  sc.camera = { eye: { x: 1.7, y: -1.7, z: 1.25 } };
  sc.aspectratio = { x: 1.3, y: 1.3, z: 0.7 };
  reactKeepingView(id, traces, surfaceLayout(sc, obs.stat != null));
}

function setView(chart, is3d) {
  view3d[chart] = is3d;
  document.querySelectorAll(`.seg button[data-chart="${chart}"]`).forEach((b) => {
    b.setAttribute("aria-pressed", String((b.dataset.mode === "3d") === is3d));
  });
  const hint = document.querySelector(`.hint3d[data-for="${chart}"]`);
  if (hint) hint.hidden = !is3d;
  if (chart === "outcome") $("fl-outcome-title").textContent = is3d ? "Breakdowns × on-time share" : "Delivery outcome (simulation)";
  const el = $(PLOT_ID[chart]);
  delete userView[PLOT_ID[chart]];
  el._mcView = false;
  Plotly.purge(el);
  el.classList.toggle("is3d", is3d);
  el.closest(".card").classList.toggle("wide3d", is3d);          // 3D gets the full width
  try { localStorage.setItem("mc-view3d", JSON.stringify(view3d)); } catch { /* storage unavailable */ }
  renderDashboard(true);
}

function checkpointAt(n) {
  let best = null;
  for (const c of fl.checkpoints) if (c.n <= n) best = c;
  return best || fl.checkpoints[0] || null;
}

// ── scenario jobs: tiles and charts built from the job's own description ─────
function setupDashboard(dash) {
  document.querySelectorAll("#gx-charts .plot").forEach((el) => Plotly.purge(el));
  Object.keys(view3d).filter((k) => k.startsWith("gx_")).forEach((k) => { delete view3d[k]; delete PLOT_ID[k]; });
  gx.dash = dash || null;
  $("fl-tiles").hidden = $("fl-charts").hidden = !!dash;
  $("gx-tiles").hidden = $("gx-charts").hidden = !dash;
  $("gx-tiles").replaceChildren();
  $("gx-charts").replaceChildren();
  $("fl-title").textContent = dash ? dash.title : "Fleet simulation";
  if (!dash) return;
  const el = (tag, cls, text, parent) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  };
  $("gx-tiles").style.gridTemplateColumns = `repeat(${dash.tiles.length}, minmax(0, 1fr))`;
  dash.tiles.forEach((t, i) => {
    const tile = el("div", "fl-tile", null, $("gx-tiles"));
    el("span", "k", t.label, tile);
    el("span", `v ${TONE_INK[t.tone] || ""}`, "–", tile).id = `gx-t-${i}`;
    el("span", "s", "\u00a0", tile).id = `gx-s-${i}`;
  });
  dash.charts.forEach((c) => {
    const chart = `gx_${c.key}`, id = `gx-c-${c.key}`;
    view3d[chart] = false;
    PLOT_ID[chart] = id;
    const card = el("div", "card", null, $("gx-charts"));
    const head = el("div", "card-head", null, card);
    el("h3", "", c.title, head);
    const seg = el("div", "seg", null, head);
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "View");
    for (const mode of ["2d", "3d"]) {
      const b = el("button", "", mode.toUpperCase(), seg);
      b.type = "button"; b.dataset.chart = chart; b.dataset.mode = mode;
      b.setAttribute("aria-pressed", String(mode === "2d"));
      b.addEventListener("click", () => setView(chart, mode === "3d"));
    }
    const hint = el("p", "hint3d", `3D: the distribution after every 10% of the simulated ${dash.unit} — later slices stop changing once the estimate is stable.`, card);
    hint.dataset.for = chart;
    hint.hidden = true;
    el("div", "plot fl-plot", null, card).id = id;
  });
  addZoomControls(dash.charts.map((c) => `gx-c-${c.key}`));
}

function renderGeneric(ck, draw3d, n) {
  const d = gx.dash;
  d.tiles.forEach((t, i) => {
    $(`gx-t-${i}`).textContent = fmtVal(ck[t.key], t.fmt, t.unit);
    $(`gx-s-${i}`).textContent = (t.sub || [])
      .map((p) => (p.text != null ? p.text : fmtVal(p.key ? ck[p.key] : p.value, p.fmt, t.unit))).join("");
  });
  d.charts.forEach((c) => {
    const id = `gx-c-${c.key}`, color = tone(c.tone), xfmt = c.fmt === "pct" ? ".0%" : "";
    if (view3d[`gx_${c.key}`]) { if (draw3d) waterfall(id, c.series, color, c.x_title, xfmt); return; }
    const lines = [];
    c.lines.forEach((l) => {
      const v = l.key ? ck[l.key] : l.value;
      if (v != null) lines.push(vline(v, tone(l.tone), `${l.label} ${fmtVal(v, l.fmt, c.unit)}`, lines.length));
    });
    histogram(id, c.series, color, c.x_title, lines, xfmt);
  });
  if (draw3d) render3d(frameAt(n));
}

function renderDashboard(force3d = false) {
  const n = fl.revealed, obs = state.observed || {};
  frameNo += 1;
  const received = (fl.series.share || []).length;
  const draw3d = force3d || frameNo % 3 === 0 || n >= received;       // 3D redraws at a third of the rate
  const ck = checkpointAt(n);
  const playing = fl.done && n < (fl.series.share || []).length;
  $("fl-progress").textContent = `· ${int(n)} / ${int(fl.total)} simulated ${unitWord()}` +
    (playing ? " · playing back the finished run" : "");
  $("progress-bar").style.width = fl.total ? `${(100 * n) / fl.total}%` : "0";
  if (!ck) return;
  if (gx.dash) { renderGeneric(ck, draw3d, n); return; }
  $("fl-avail").textContent = pct(ck.fleet_availability_mean);
  $("fl-ontime").textContent = pct(ck.p_delivery_within_target);
  $("fl-fuel").textContent = `€${int(ck.fuel_cost_mean)}`;
  $("fl-fuel-s").textContent = `${int(ck.fuel_l_mean)} L per day`;
  $("fl-break").textContent = pct(ck.p_breakdowns_over_alert);
  $("fl-break-s").textContent = `P(> ${ck.breakdown_alert} breakdowns per day)`;
  $("fl-sla").textContent = pct(ck.p_meet_sla);
  $("fl-sla-s").textContent = obs.stat_label ? `${obs.stat_label} of deliveries on time` : "";

  const warm = cssVar("--surrogate"), bad = cssVar("--bad"), good = cssVar("--good"), blue = cssVar("--observed");
  if (view3d.vehicles) { if (draw3d) waterfall("fl-vehicles", "vehicles_required", blue, "number of vehicles"); }
  else histogram("fl-vehicles", "vehicles_required", blue, "number of vehicles", [
    vline(ck.vehicles_required_mean, warm, `expected ${int(ck.vehicles_required_mean)}`, 0),
    vline(ck.vehicles_required_p95, bad, `P95 ${int(ck.vehicles_required_p95)}`, 1),
    ...(obs.headline != null ? [vline(obs.headline, good, `fleet ${obs.headline}`, 2)] : []),
  ]);
  if (view3d.maint) { if (draw3d) waterfall("fl-maint", "maint_cost", good, "daily maintenance cost (€)"); }
  else histogram("fl-maint", "maint_cost", good, "daily maintenance cost (€)", [
    vline(ck.maint_cost_mean, warm, `expected €${int(ck.maint_cost_mean)}`, 0),
    vline(ck.maint_cost_p95, bad, `P95 €${int(ck.maint_cost_p95)}`, 1),
  ]);
  if (view3d.share) { if (draw3d) waterfall("fl-share", "share", warm, "on-time share of the day", ".0%"); }
  else histogram("fl-share", "share", warm, "on-time share of the day",
    obs.stat != null ? [vline(obs.stat, blue, obs.stat_label || "SLA", 0)] : [], ".0%");
  if (draw3d) render3d(frameAt(n));                           // Mapper: groups of the day being shown
  if (view3d.outcome) { if (draw3d) joint("fl-outcome"); return; }
  const on = ck.on_time_share_mean;
  Plotly.react("fl-outcome", [{ type: "pie", hole: 0.55, sort: false, labels: ["On time", "Delayed"],
      values: [on, 1 - on], marker: { colors: [good, bad] }, textinfo: "percent",
      textfont: { color: "#fff", size: 13 }, hovertemplate: "%{label}: %{percent}<extra></extra>" }], {
    paper_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 11 },
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: true, legend: { orientation: "h", y: -0.02 },
  }, PLOT_CONFIG);
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
  Object.keys(inc).forEach((k) => delete inc[k]);
  Object.assign(fl, { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false, paused: false });
  Object.assign(state, { received: 0, observed: null, frame: null, frames: [], shownFrame: null, status: null, plotReady: false });
  ["fl-vehicles", "fl-outcome", "fl-maint", "fl-share", "plot3d"].forEach((id) => Plotly.purge(id));
  setupDashboard(null);
  ["fl-avail", "fl-ontime", "fl-fuel", "fl-break", "fl-sla"].forEach((id) => { $(id).textContent = "–"; });
  $("fl-report").hidden = true;
  $("fl-replay").disabled = true;
  $("fl-pause").disabled = true;
  $("mapper-key").hidden = true;
  $("progress-bar").style.width = "0";
  $("fl-progress").textContent = "–";
}

async function poll(generation) {
  if (generation !== state.generation) return;
  try {
    const view = await api(`/v1/jobs/${state.jobId}/live?since=${state.received}`);
    if (generation !== state.generation) return;
    state.status = view.status;
    const feed = view.live.fleet || view.live.scenario;
    const p = (view.progress && (view.progress.fleet || view.progress.scenario)) || { done: 0, total: 0 };
    const errs = Object.entries(view.errors || {}).map(([t, e]) => `${t}: ${e}`).join(" · ");
    $("status-line").textContent = `simulation ${view.status} · ${int(p.done)} / ${int(p.total)} ${unitWord()} computed in ${view.elapsed_s.toFixed(1)} s · ${view.dataset_id}` + (errs ? ` · ${errs}` : "");
    $("cancel").disabled = !ACTIVE.includes(view.status);
    if (!feed) {
      showError("This job has no simulation to play back (only fleet and scenario jobs are shown here).");
      return;
    }
    if (feed.null_from !== state.received) {                  // out of sync → restart from 0
      resetView();
      schedule(generation);
      return;
    }
    if (feed.observed) {
      state.observed = feed.observed;
      setupDashboard(feed.observed.dashboard);
    }
    for (const [k, v] of Object.entries(feed.series || {})) (fl.series[k] = fl.series[k] || []).push(...v);
    (fl.series.share = fl.series.share || []).push(...feed.null);
    state.received += feed.null.length;
    fl.checkpoints = feed.checkpoints || fl.checkpoints;
    fl.total = p.total || fl.total;
    state.frame = feed.frame;
    state.frames = feed.frames || state.frames;
    if (!fl.revealed) render3d();
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
    const jobs = (await api("/v1/jobs")).filter((j) => j.tests.some((t) => t === "fleet" || t === "scenario"));
    const sel = $("job-select");
    sel.replaceChildren();
    if (!jobs.length) sel.add(new Option("— no jobs yet —", ""));
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

// ── zoom (every chart) ──────────────────────────────────────────────────────
// 3D: move the camera closer / further; 2D: shrink / grow the axis ranges around
// their centre. Reset: the chart's default view. Scroll / pinch zoom as well.
function zoomChart(id, factor) {
  const el = $(id);
  if (!el || !el._fullLayout) return;
  if (id === "plot3d") {                                   // the rotating TDA view keeps its zoom in state.eye
    state.eye.r *= factor;
    state.eye.z *= factor;
    pauseRotation();
    Plotly.relayout(el, { "scene.camera.eye": eyeXYZ() });
    return;
  }
  const v = (userView[id] = userView[id] || {});
  const scene = el._fullLayout.scene;
  if (scene && scene._scene) {
    const e = (scene.camera && scene.camera.eye) || { x: 1.25, y: 1.25, z: 1.25 };
    v.camera = { ...(v.camera || {}), eye: { x: e.x * factor, y: e.y * factor, z: e.z * factor } };
    Plotly.relayout(el, { "scene.camera.eye": v.camera.eye });
    return;
  }
  const upd = {};
  for (const [ax, key] of [["xaxis", "x"], ["yaxis", "y"]]) {
    const r = el._fullLayout[ax] && el._fullLayout[ax].range;
    if (!r) continue;
    const c = (r[0] + r[1]) / 2, h = ((r[1] - r[0]) / 2) * factor;
    v[key] = [c - h, c + h];
    upd[`${ax}.range`] = v[key];
  }
  if (Object.keys(upd).length) Plotly.relayout(el, upd);
}

function resetChart(id) {
  const el = $(id);
  if (!el || !el._fullLayout) return;
  if (id === "plot3d") {
    const e = MapperView.VIEWS[state.tdaView].eye;
    Object.assign(state.eye, { r: Math.hypot(e.x, e.y), z: e.z });
    Plotly.relayout(el, { "scene.camera.eye": eyeXYZ() });
    return;
  }
  delete userView[id];
  Plotly.purge(el);                                        // drop the user's zoom, redraw the default view
  Object.keys(inc).forEach((k) => delete inc[k]);
  renderDashboard(true);
}

function addZoomControls(ids = ["fl-vehicles", "fl-outcome", "fl-maint", "fl-share", "plot3d"]) {
  for (const id of ids) {
    const plot = $(id);
    const head = plot && plot.closest(".card").querySelector(".card-head");
    if (!head || head.querySelector(".zoom")) continue;
    const box = document.createElement("div");
    box.className = "zoom";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Zoom");
    [["+", "Zoom in", () => zoomChart(id, 0.8)], ["−", "Zoom out", () => zoomChart(id, 1.25)],
     ["Reset", "Reset view", () => resetChart(id)]].forEach(([text, title, fn]) => {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = text; b.title = title; b.setAttribute("aria-label", title);
      b.addEventListener("click", fn);
      box.appendChild(b);
    });
    head.appendChild(box);
  }
}

// ── camera rotation (TDA view) ──────────────────────────────────────────────
// Continues from wherever the user left the camera (zoom included) and pauses
// while they drag, scroll or pinch, and for a few seconds after.
let lastTurn = 0;
let pausedUntil = 0;
function pauseRotation(ms = 4000) { pausedUntil = performance.now() + ms; }

function trackUserCamera() {
  const el = $("plot3d");
  if (!el || el._mcTracked || !el.on) return;
  el._mcTracked = true;
  el.on("plotly_relayouting", () => pauseRotation());
  el.on("plotly_relayout", (ev) => {
    const eye = ev && ((ev["scene.camera"] && ev["scene.camera"].eye) || ev["scene.camera.eye"]);
    if (!eye || performance.now() - lastTurn < 25) return;        // our own rotation step
    Object.assign(state.eye, { angle: Math.atan2(eye.y, eye.x), r: Math.hypot(eye.x, eye.y), z: eye.z });
    pauseRotation();
  });
}

function rotate(ts) {
  if ($("rotate").checked && state.plotReady && ts - lastTurn > 50 && ts > pausedUntil) {
    trackUserCamera();
    lastTurn = performance.now();
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
  document.querySelectorAll(".seg button[data-chart]").forEach((b) => {
    b.addEventListener("click", () => setView(b.dataset.chart, b.dataset.mode === "3d"));
  });
  document.querySelectorAll(".seg button[data-tda]").forEach((b) => b.addEventListener("click", () => setTdaView(b.dataset.tda)));
  try { setTdaView(localStorage.getItem("mc-tda-view") || "mapper"); } catch { /* storage unavailable */ }
  try {
    const saved = JSON.parse(localStorage.getItem("mc-view3d") || "null");
    if (saved) Object.entries(saved).forEach(([k, v]) => { if (k in view3d && v) setView(k, true); });
  } catch { /* storage unavailable */ }
  $("fl-pause").addEventListener("click", () => {
    fl.paused = !fl.paused;
    $("fl-pause").textContent = fl.paused ? "Resume" : "Pause";
  });
  try {
    const saved = localStorage.getItem("mc-speed");
    if (saved) $("speed").value = saved;
  } catch { /* storage unavailable */ }
  $("speed").addEventListener("change", (e) => { try { localStorage.setItem("mc-speed", e.target.value); } catch { /* ignore */ } });
  addZoomControls();
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
  if (wanted && !linked) showError(`Job ${wanted} was not found (finished jobs expire after the configured TTL).`);
  const first = linked || jobs.find((j) => ACTIVE.includes(j.status)) || jobs[0];
  if (first) {
    $("job-select").value = first.job_id;
    selectJob(first.job_id);
  }
}

init();
