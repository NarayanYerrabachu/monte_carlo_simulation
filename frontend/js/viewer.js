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

// TDA Mapper graph in 3D, laid out exactly like CortXplorer's TDA Mapper view:
// topological spread on x / y, filter height (lens) on z. Node size = records,
// colour = on-time share of the node's deliveries (from the data). Orange marks
// the groups holding the vehicles of the simulated day currently shown.
function renderMapper3d(frame) {
  const g = state.observed.graph;
  const byId = new Map(g.nodes.map((nd) => [nd.id, nd]));
  const maxSize = Math.max(...g.nodes.map((nd) => nd.size));
  const ex = [], ey = [], ez = [];
  for (const [a, b] of g.edges) {
    const p = byId.get(a), q = byId.get(b);
    if (!p || !q) continue;
    ex.push(p.x, q.x, null); ey.push(p.y, q.y, null); ez.push(p.z, q.z, null);
  }
  const radius = (size) => 3 + 20 * Math.sqrt(size / maxSize);
  // Labels like CortXplorer's TDA Mapper, readable in 3D: each distinct label once (on its
  // largest group), numbered in the plot and spelled out in the table beside it.
  const RISK = 0.7, MAX_LABELS = 12;
  const risky = (nd) => nd.on_time != null && nd.on_time < RISK;
  const bySize = [...g.nodes].sort((a, b) => b.size - a.size);
  const seen = new Set(), shown = [];
  for (const nd of bySize) {                      // largest groups first, then the largest at-risk ones
    const key = nd.label || `group ${nd.id}`;
    if (shown.length >= MAX_LABELS) break;
    if (seen.has(key) || nd.size < 20) continue;
    if (shown.length < 8 || risky(nd)) { seen.add(key); shown.push(nd); }
  }
  renderMapperKey(shown, risky);
  const traces = [
    { type: "scatter3d", mode: "lines", x: ex, y: ey, z: ez, line: { color: cssVar("--muted"), width: 1.5 },
      hoverinfo: "skip", name: "shared records", showlegend: false },
    { type: "scatter3d", mode: "markers", name: "Mapper groups",
      x: g.nodes.map((nd) => nd.x), y: g.nodes.map((nd) => nd.y), z: g.nodes.map((nd) => nd.z),
      marker: { size: g.nodes.map((nd) => radius(nd.size)), color: g.nodes.map((nd) => nd.on_time), cmin: 0, cmax: 1,
                colorscale: [[0, "#d03b3b"], [0.6, "#f59e0b"], [0.9, "#8bc34a"], [1, "#15803d"]], opacity: 0.85,
                line: { width: 0 }, colorbar: { title: { text: "on-time", side: "right" }, tickformat: ".0%", len: 0.6, thickness: 10 } },
      text: g.nodes.map((nd) => `<b>${nd.label || `group ${nd.id}`}</b><br>group ${nd.id} · ${int(nd.size)} records<br>on time ${pct(nd.on_time)} · breakdowns ${pct(nd.breakdown_rate)} · availability ${pct(nd.availability)}`),
      hovertemplate: "%{text}<extra></extra>" },
    { type: "scatter3d", mode: "text", name: "labels", showlegend: false, hoverinfo: "skip",
      x: shown.map((nd) => nd.x), y: shown.map((nd) => nd.y), z: shown.map((nd) => nd.z),
      text: shown.map((nd, i) => `${risky(nd) ? "⚠" : ""}${i + 1}`),
      textposition: "middle right", textfont: { size: 14, color: cssVar("--ink"), family: "system-ui, sans-serif" } },
  ];
  if (frame && frame.nodes && frame.nodes.length) {
    const hit = frame.nodes.map(([id, c]) => [byId.get(id), c]).filter(([nd]) => nd);
    traces.push({ type: "scatter3d", mode: "markers", name: `Vehicles of a simulated day (${frame.day})`,
      x: hit.map(([nd]) => nd.x), y: hit.map(([nd]) => nd.y), z: hit.map(([nd]) => nd.z),
      marker: { size: hit.map(([nd]) => radius(nd.size) + 6), color: cssVar("--surrogate"), opacity: 0.55,
                line: { color: cssVar("--surrogate"), width: 2 } },
      text: hit.map(([nd, c]) => `${c} vehicle${c === 1 ? "" : "s"} of this day in <b>${nd.label || `group ${nd.id}`}</b> (group ${nd.id})`),
      hovertemplate: "%{text}<extra></extra>" });
  }
  const muted = cssVar("--muted"), grid = cssVar("--line");
  const axis = (t) => ({ showbackground: false, gridcolor: grid, zerolinecolor: grid, color: muted, title: { text: t } });
  Plotly.react("plot3d", traces, {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: { color: cssVar("--ink"), size: 12 },
    margin: { l: 0, r: 0, t: 8, b: 30 }, showlegend: true,
    legend: { orientation: "h", x: 0, y: 0, yanchor: "top", bgcolor: "rgba(0,0,0,0)", font: { size: 11 } },
    scene: { xaxis: axis("← Topological spread →"), yaxis: axis("← Topological spread →"),
             zaxis: axis("Filter height (how different from average)"), aspectmode: "cube", camera: { eye: eyeXYZ() } },
    uirevision: "keep",
  }, { displaylogo: false, responsive: true });
  state.plotReady = true;
}

function renderMapperKey(shown, risky) {
  const table = $("mapper-table");
  table.replaceChildren();
  $("mapper-key").hidden = !shown.length;
  $("plot3d").parentElement.classList.toggle("has-key", shown.length > 0);
  if (!shown.length) return;
  const head = table.createTHead().insertRow();
  ["#", "Group", "Records", "On time", "Breakdowns"].forEach((h) => {
    const th = document.createElement("th"); th.textContent = h; head.appendChild(th);
  });
  const body = table.createTBody();
  shown.forEach((nd, i) => {
    const tr = body.insertRow();
    const num = tr.insertCell(); num.textContent = `${risky(nd) ? "⚠ " : ""}${i + 1}`;
    if (risky(nd)) num.className = "risk";
    tr.insertCell().textContent = nd.label || `group ${nd.id}`;
    tr.insertCell().textContent = int(nd.size);
    tr.insertCell().textContent = pct(nd.on_time);
    tr.insertCell().textContent = pct(nd.breakdown_rate);
  });
}

function components(g) {                                     // connected clusters of the Mapper graph
  const parent = new Map(g.nodes.map((nd) => [nd.id, nd.id]));
  const find = (a) => { while (parent.get(a) !== a) { parent.set(a, parent.get(parent.get(a))); a = parent.get(a); } return a; };
  for (const [a, b] of g.edges) if (parent.has(a) && parent.has(b)) parent.set(find(a), find(b));
  return new Set(g.nodes.map((nd) => find(nd.id))).size;
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
    $("cloud-title").textContent = "TDA Mapper — 3D shape of the fleet data";
    const g = obs.graph;
    const records = g.nodes.reduce((a, nd) => a + nd.size, 0);
    $("cloud-hint").textContent = `${int(g.nodes.length)} groups · ${int(g.edges.length)} connections · ${int(components(g))} separate clusters · ${int(records)} records (a record can sit in overlapping groups). Size = records, colour = on-time share; labels name the largest groups and the groups at risk (⚠ below 70 % on time). Orange: the groups holding the vehicles of the simulated day being shown.`;
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
      type: "scatter3d", mode: "lines", name: `after ${int(r.n)} days`,
      x, y: x.map(() => r.n), z,
      line: { color: rgba(color, Math.min(1, shade + 0.1)), width: j === rows.length - 1 ? 6 : 3 },
      hovertemplate: `%{x:,.2f} after ${int(r.n)} days: %{z:.1%}<extra></extra>`,
      showlegend: j === 0 || j === rows.length - 1,
    });
  });
  Plotly.react(id, traces, surfaceLayout(scene(xTitle, "simulated days", "probability", xfmt), true),
               { displaylogo: false, responsive: true });
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
  Plotly.react(id, traces, surfaceLayout(sc, obs.stat != null), { displaylogo: false, responsive: true });
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

function renderDashboard(force3d = false) {
  const n = fl.revealed, obs = state.observed || {};
  frameNo += 1;
  const received = (fl.series.share || []).length;
  const draw3d = force3d || frameNo % 3 === 0 || n >= received;       // 3D redraws at a third of the rate
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
  Object.keys(inc).forEach((k) => delete inc[k]);
  Object.assign(fl, { series: {}, checkpoints: [], revealed: 0, total: 0, timer: null, done: false, paused: false });
  Object.assign(state, { received: 0, observed: null, frame: null, frames: [], shownFrame: null, status: null, plotReady: false });
  ["fl-vehicles", "fl-outcome", "fl-maint", "fl-share", "plot3d"].forEach((id) => Plotly.purge(id));
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
  document.querySelectorAll(".seg button").forEach((b) => {
    b.addEventListener("click", () => setView(b.dataset.chart, b.dataset.mode === "3d"));
  });
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
