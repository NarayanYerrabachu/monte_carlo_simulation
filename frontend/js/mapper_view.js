// TDA Mapper graph in 3D — shared by the live viewer and the job report.
//
// Laid out exactly like CortXplorer's TDA Mapper view: topological spread on
// x / y, filter height (lens) on z, node size = records. How the groups are
// coloured, flagged and described comes from the job:
//   scenario jobs  send graph.view (colour key, risk threshold, table columns)
//   fleet jobs     use the on-time share of the group's deliveries
// Everything here is pure: it builds Plotly traces / layouts and fills a table.

const MapperView = (() => {
  const pct = (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
  const int = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-GB"));
  const num = (v) => {
    if (v == null || !Number.isFinite(v)) return "–";
    const a = Math.abs(v), d = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : 3;
    return v.toLocaleString("en-GB", { maximumFractionDigits: d });
  };
  const fmt = (v, f) => (f === "pct" ? pct(v) : f === "int" ? int(v) : num(v));
  const name = (nd) => nd.label || `group ${nd.id}`;
  // same marker sizing as CortXplorer's TDA Mapper 3D view
  const radius = (size) => Math.max(5, Math.min(28, Math.sqrt(size) * 2.4));

  function graphView(g) {
    const v = g.view;
    if (!v) {
      return {
        color: (nd) => (nd.on_time == null ? 0 : 1 - nd.on_time), colorTitle: "late deliveries", colorFmt: ".0%", colorMax: 1,
        risky: (nd) => nd.on_time != null && nd.on_time < 0.7,
        columns: [["On time", (nd) => pct(nd.on_time)], ["Breakdowns", (nd) => pct(nd.breakdown_rate)]],
        hover: (nd) => `on time ${pct(nd.on_time)} · breakdowns ${pct(nd.breakdown_rate)} · availability ${pct(nd.availability)}`,
        inPeriod: (c, frame) => `${c} vehicle${c === 1 ? "" : "s"} of the simulated day (${frame.day})`,
        title: "TDA Mapper — 3D shape of the fleet data",
        legend: "colour = share of late deliveries (teal on time → red late)", riskNote: "⚠ below 70 % on time",
        ring: "the groups holding the vehicles of the simulated day being shown",
      };
    }
    const get = (nd, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), nd);
    const columns = v.columns.map((c) => [c.label, (nd) => fmt(get(nd, c.key), c.fmt)]);
    return {
      color: (nd) => get(nd, v.color_key) ?? 0, colorTitle: v.color_title, colorFmt: ".2f", colorMax: v.color_max || 1,
      risky: (nd) => (get(nd, v.color_key) ?? 0) >= v.risk_above,
      columns, hover: (nd) => columns.map(([label, f]) => `${label} ${f(nd)}`).join(" · "),
      inPeriod: (c, frame) => `${c} of the ${v.unit} of the simulated ${v.period_label} (${frame.day})`,
      title: "TDA Mapper — 3D shape of the data",
      legend: `colour = average ${v.color_title} (teal low → red high)`, riskNote: `⚠ ${v.color_title} ≥ ${v.risk_above}`,
      ring: `the groups holding the ${v.unit} of the simulated ${v.period_label} being shown`,
    };
  }

  // Labelled groups: each distinct label once (on its largest group) — the 8 largest
  // plus the groups at risk, at most 12. Numbered in the plot, spelled out in the table.
  function labelled(g, gv, max = 12) {
    const seen = new Set(), shown = [];
    for (const nd of [...g.nodes].sort((a, b) => b.size - a.size)) {
      if (shown.length >= max) break;
      if (seen.has(name(nd)) || nd.size < 20) continue;
      if (shown.length < 8 || gv.risky(nd)) { seen.add(name(nd)); shown.push(nd); }
    }
    return shown;
  }

  function components(g) {                                     // connected clusters of the graph
    const parent = new Map(g.nodes.map((nd) => [nd.id, nd.id]));
    const find = (a) => { while (parent.get(a) !== a) { parent.set(a, parent.get(parent.get(a))); a = parent.get(a); } return a; };
    for (const [a, b] of g.edges) if (parent.has(a) && parent.has(b)) parent.set(find(a), find(b));
    return new Set(g.nodes.map((nd) => find(nd.id))).size;
  }

  // frame (optional): {day, nodes: [[group id, records of the simulated period in it], …]}
  function traces(g, gv, shown, frame) {
    const byId = new Map(g.nodes.map((nd) => [nd.id, nd]));
    const ex = [], ey = [], ez = [];
    for (const [a, b] of g.edges) {
      const p = byId.get(a), q = byId.get(b);
      if (!p || !q) continue;
      ex.push(p.x, q.x, null); ey.push(p.y, q.y, null); ez.push(p.z, q.z, null);
    }
    const rank = new Map(shown.map((nd, i) => [nd.id, i + 1]));
    const out = [
      // connections: brighter than CortXplorer's #3A4050 so they stay visible on the dark scene
      { type: "scatter3d", mode: "lines", x: ex, y: ey, z: ez, line: { color: "rgba(170,182,204,0.75)", width: 2.5 },
        hoverinfo: "skip", showlegend: false },
      { type: "scatter3d", mode: "markers+text", showlegend: false,
        x: g.nodes.map((nd) => nd.x), y: g.nodes.map((nd) => nd.y), z: g.nodes.map((nd) => nd.z),
        text: g.nodes.map((nd) => (rank.has(nd.id) ? `${gv.risky(nd) ? "⚠" : ""}${rank.get(nd.id)}` : "")),
        textposition: "top center",
        textfont: { size: 12, color: "rgba(217,220,227,0.95)", family: "IBM Plex Mono, monospace" },
        // CortXplorer's teal → purple → red scale, here for the job's risk measure
        marker: { size: g.nodes.map((nd) => radius(nd.size)), color: g.nodes.map(gv.color), cmin: 0, cmax: gv.colorMax,
                  colorscale: [[0, "#5E9CA6"], [0.5, "#9B7FD4"], [1, "#E05252"]], opacity: 0.88,
                  line: { width: 1.5, color: "rgba(15,17,23,0.7)" },
                  colorbar: { title: { text: gv.colorTitle, side: "right", font: { color: "#878E9C", size: 11 } },
                              tickformat: gv.colorFmt, tickfont: { color: "#878E9C", size: 10 }, len: 0.55, thickness: 10,
                              outlinewidth: 0 } },
        customdata: g.nodes.map((nd) => `<b>${name(nd)}</b><br>${nd.profile || ""}<br>group ${nd.id} · ${int(nd.size)} records<br>${gv.hover(nd)}`),
        hovertemplate: "%{customdata}<extra></extra>" },
    ];
    if (frame && frame.nodes && frame.nodes.length) {
      const hit = frame.nodes.map(([id, c]) => [byId.get(id), c]).filter(([nd]) => nd);
      out.push({ type: "scatter3d", mode: "markers", showlegend: false,
        x: hit.map(([nd]) => nd.x), y: hit.map(([nd]) => nd.y), z: hit.map(([nd]) => nd.z),
        marker: { size: hit.map(([nd]) => radius(nd.size) + 7), color: "rgba(0,0,0,0)", opacity: 1,
                  line: { color: "#F5A623", width: 5 } },
        customdata: hit.map(([nd, c]) => `${gv.inPeriod(c, frame)} in <b>${name(nd)}</b>`),
        hovertemplate: "%{customdata}<extra></extra>" });
    }
    return out;
  }

  function layout(g, eye) {
    const records = g.nodes.reduce((acc, nd) => acc + nd.size, 0);
    const axis = (title, ticks) => ({ showgrid: true, gridcolor: ticks ? "#2A2F3A" : "#1E2330", showticklabels: ticks,
      tickfont: { color: "#59616E", size: 9 }, zeroline: ticks, zerolinecolor: "#3A4050", backgroundcolor: "#0F1117",
      showbackground: true, showspikes: false, title: { text: title, font: { color: ticks ? "#878E9C" : "#59616E", size: 11 } } });
    return {
      title: { text: `TDA Mapper — 3D Shape of Your Data<br><span style="font-size:11px;color:#878E9C">${int(g.nodes.length)} groups · ${int(g.edges.length)} connections · ${int(components(g))} separate clusters · ${int(records)} total records</span>`,
               font: { color: "#D9DCE3", size: 15, family: "IBM Plex Mono, monospace" }, x: 0.5, xanchor: "center" },
      paper_bgcolor: "#0F1117", plot_bgcolor: "#0F1117",
      font: { color: "#D9DCE3", family: "IBM Plex Mono, monospace" },
      margin: { l: 0, r: 0, t: 70, b: 10 }, showlegend: false,
      scene: { bgcolor: "#0F1117", xaxis: axis("← Topological spread →", false), yaxis: axis("← Topological spread →", false),
               zaxis: axis("Filter height (how different from average)", true), aspectmode: "cube",
               camera: { eye, center: { x: 0, y: 0, z: 0 } } },
      uirevision: "keep",
    };
  }

  // ── Galaxy view ────────────────────────────────────────────────────────────
  // The same groups and connections drawn like CortXplorer's Galaxy view: the
  // shape flattened into a disk, a starfield behind it, nebula gas around the
  // groups and along the connections, and a glow per group. Colour: gold (low
  // risk) → teal → red (high risk), on the job's own risk measure.
  const GALAXY_BG = "#000008";
  const DISK = 0.4;                                            // z flattened to 40 %: the galaxy disk

  function rng(seed) {                                         // small seeded generator (mulberry32)
    let a = seed >>> 0;
    const next = () => {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    const normal = () => Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
    return { next, normal, uniform: (lo, hi) => lo + (hi - lo) * next() };
  }

  function galaxyRgb(t) {                                      // gold core → teal arm → supernova red
    const mix = (a, b, u) => a.map((v, i) => Math.round(v + (b[i] - v) * u));
    if (t < 0.4) return mix([255, 200, 50], [0, 220, 200], t / 0.4);
    if (t < 0.72) return mix([0, 220, 200], [210, 70, 50], (t - 0.4) / 0.32);
    return mix([210, 70, 50], [255, 30, 20], Math.min(1, (t - 0.72) / 0.28));
  }
  const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;

  // Stars and gas depend only on the graph: built once per graph, reused on every redraw.
  const backdrops = new WeakMap();

  function backdrop(g, gv) {
    if (backdrops.has(g)) return backdrops.get(g);
    const r = rng(77);
    const span = Math.max(1e-6, ...g.nodes.map((nd) => Math.hypot(nd.x, nd.y)));
    const tint = (nd) => galaxyRgb(Math.min(1, gv.color(nd) / (gv.colorMax || 1)));
    // starfield: a flattened shell around the galaxy
    const star = { x: [], y: [], z: [], c: [], s: [] };
    for (let i = 0; i < 420; i++) {
      const rad = span * r.uniform(1.1, 1.8), th = r.uniform(0, 2 * Math.PI), ph = r.uniform(0, Math.PI);
      star.x.push(rad * Math.sin(ph) * Math.cos(th)); star.y.push(rad * Math.sin(ph) * Math.sin(th));
      star.z.push(rad * Math.cos(ph) * 0.18);
      star.c.push(`rgba(255,255,255,${r.uniform(0.3, 0.9).toFixed(2)})`); star.s.push(r.uniform(0.6, 2.2));
    }
    // gas: a cool haze over the disk, a cloud in each group's colour, and streaks along the connections
    const gas = { x: [], y: [], z: [], c: [], s: [] };
    const add = (x, y, z, c, s) => { gas.x.push(x); gas.y.push(y); gas.z.push(z); gas.c.push(c); gas.s.push(s); };
    for (let i = 0; i < 360; i++) {                          // dense at the centre, thinning out: no hard edge
      const rad = span * 0.6 * Math.abs(r.normal()), th = r.uniform(0, 2 * Math.PI);
      add(rad * Math.cos(th), rad * Math.sin(th), r.normal() * 0.05,
          css([r.uniform(60, 100), r.uniform(80, 130), r.uniform(190, 235)].map(Math.round)), r.uniform(30, 60));
    }
    const lift = (c) => c.map((v) => Math.round(v + (255 - v) * 0.1));
    for (const nd of g.nodes) {
      const px = radius(nd.size), k = Math.max(4, Math.min(18, Math.round(px * 0.5)));
      const sigma = span * (0.035 + px * 0.003), col = css(lift(tint(nd)));
      for (let i = 0; i < k; i++) add(nd.x + r.normal() * sigma, nd.y + r.normal() * sigma, nd.z * DISK + r.normal() * sigma * 0.5, col, r.uniform(10, 30));
    }
    const byId = new Map(g.nodes.map((nd) => [nd.id, nd]));
    for (const [a, b] of g.edges) {
      const p = byId.get(a), q = byId.get(b);
      if (!p || !q) continue;
      const len = Math.hypot(q.x - p.x, q.y - p.y, (q.z - p.z) * DISK);
      const k = Math.max(4, Math.min(24, Math.round((len / span) * 40))), cp = tint(p), cq = tint(q);
      for (let i = 0; i < k; i++) {
        const t = r.uniform(0.08, 0.92), j = span * 0.025;
        add(p.x + t * (q.x - p.x) + r.normal() * j, p.y + t * (q.y - p.y) + r.normal() * j,
            (p.z + t * (q.z - p.z)) * DISK + r.normal() * j * 0.5, css(cp.map((v, n) => Math.round(v + (cq[n] - v) * t))), r.uniform(8, 20));
      }
    }
    const built = { star, gas };
    backdrops.set(g, built);
    return built;
  }

  function galaxyTraces(g, gv, shown, frame) {
    const { star, gas } = backdrop(g, gv);
    const byId = new Map(g.nodes.map((nd) => [nd.id, nd]));
    const ex = [], ey = [], ez = [];
    for (const [a, b] of g.edges) {
      const p = byId.get(a), q = byId.get(b);
      if (!p || !q) continue;
      ex.push(p.x, q.x, null); ey.push(p.y, q.y, null); ez.push(p.z * DISK, q.z * DISK, null);
    }
    const rank = new Map(shown.map((nd, i) => [nd.id, i + 1]));
    const xs = g.nodes.map((nd) => nd.x), ys = g.nodes.map((nd) => nd.y), zs = g.nodes.map((nd) => nd.z * DISK);
    const colors = g.nodes.map((nd) => css(galaxyRgb(Math.min(1, gv.color(nd) / (gv.colorMax || 1)))));
    const sizes = g.nodes.map((nd) => radius(nd.size));
    const hover = g.nodes.map((nd) => `<b>${name(nd)}</b><br>${nd.profile || ""}<br>group ${nd.id} · ${int(nd.size)} records<br>${gv.hover(nd)}`);
    const out = [
      { type: "scatter3d", mode: "markers", x: star.x, y: star.y, z: star.z, marker: { size: star.s, color: star.c },
        hoverinfo: "skip", showlegend: false },
      // trace-level opacity: Plotly's WebGL ignores per-point alpha; overlapping blobs add up to the glow
      { type: "scatter3d", mode: "markers", x: gas.x, y: gas.y, z: gas.z,
        marker: { size: gas.s, color: gas.c, opacity: 0.05, line: { width: 0 } }, hoverinfo: "skip", showlegend: false },
      { type: "scatter3d", mode: "lines", x: ex, y: ey, z: ez, line: { color: "rgba(150,185,235,0.5)", width: 1.5 },
        hoverinfo: "skip", showlegend: false },
      // glow: carries the group's hover text too, so pointing at the glow still names the group
      { type: "scatter3d", mode: "markers", x: xs, y: ys, z: zs, showlegend: false,
        marker: { size: sizes.map((s) => s * 2.2), color: colors, opacity: 0.12, line: { width: 0 } },
        customdata: hover, hovertemplate: "%{customdata}<extra></extra>" },
      { type: "scatter3d", mode: "markers+text", x: xs, y: ys, z: zs, showlegend: false,
        text: g.nodes.map((nd) => (rank.has(nd.id) ? `${gv.risky(nd) ? "⚠" : ""}${rank.get(nd.id)}` : "")),
        textposition: "top center", textfont: { size: 12, color: "rgba(255,240,200,0.95)", family: "IBM Plex Mono, monospace" },
        marker: { size: sizes, color: colors, opacity: 0.95, line: { width: 0.5, color: "rgba(0,0,0,0.4)" } },
        customdata: hover, hovertemplate: "%{customdata}<extra></extra>" },
    ];
    if (frame && frame.nodes && frame.nodes.length) {
      const hit = frame.nodes.map(([id, c]) => [byId.get(id), c]).filter(([nd]) => nd);
      out.push({ type: "scatter3d", mode: "markers", showlegend: false,
        x: hit.map(([nd]) => nd.x), y: hit.map(([nd]) => nd.y), z: hit.map(([nd]) => nd.z * DISK),
        marker: { size: hit.map(([nd]) => radius(nd.size) + 7), color: "rgba(0,0,0,0)", opacity: 1,
                  line: { color: "#FFFFFF", width: 4 } },
        customdata: hit.map(([nd, c]) => `${gv.inPeriod(c, frame)} in <b>${name(nd)}</b>`),
        hovertemplate: "%{customdata}<extra></extra>" });
    }
    return out;
  }

  function galaxyLayout(g, gv, eye) {
    const records = g.nodes.reduce((acc, nd) => acc + nd.size, 0);
    const axis = { showgrid: false, zeroline: false, showticklabels: false, backgroundcolor: GALAXY_BG,
                   showbackground: true, showspikes: false, title: { text: "" } };
    return {
      title: { text: `TDA Galaxy — Data Universe<br><span style="font-size:11px;color:#878E9C">${int(g.nodes.length)} star clusters · ${int(g.edges.length)} gravitational links · ${int(components(g))} galaxies · ${int(records)} records</span>`,
               font: { color: "#FFD580", size: 16, family: "IBM Plex Mono, monospace" }, x: 0.5, xanchor: "center" },
      paper_bgcolor: GALAXY_BG, plot_bgcolor: GALAXY_BG, font: { color: "#D9DCE3", family: "IBM Plex Mono, monospace" },
      margin: { l: 0, r: 0, t: 70, b: 10 }, showlegend: false,
      annotations: [{ text: `⬤ <b style="color:#FFD700">Gold</b> = low ${gv.colorTitle}  ⬤ <b style="color:#00DDCC">Teal</b> = medium  ⬤ <b style="color:#FF4422">Red</b> = high`,
                      x: 0.5, y: 0.01, xref: "paper", yref: "paper", showarrow: false,
                      font: { size: 11, color: "#878E9C", family: "IBM Plex Mono, monospace" }, bgcolor: "rgba(0,0,8,0.7)", borderpad: 6 }],
      scene: { bgcolor: GALAXY_BG, xaxis: axis, yaxis: axis, zaxis: axis, aspectmode: "manual", aspectratio: { x: 1, y: 1, z: 0.6 },
               camera: { eye, center: { x: 0, y: 0, z: 0 } } },
      uirevision: "keep",
    };
  }

  // The two views of the same graph: default camera, traces, layout and the legend sentence.
  const VIEWS = {
    mapper: { eye: { x: 1.6, y: 1.6, z: 0.9 }, traces, layout: (g, gv, eye) => layout(g, eye),
              legend: (gv) => gv.legend, ring: "Orange rings" },
    galaxy: { eye: { x: 0.5, y: 0.5, z: 0.42 }, traces: galaxyTraces, layout: galaxyLayout,
              legend: (gv) => `colour = ${gv.colorTitle} (gold low → teal → red high)`, ring: "White rings" },
  };

  // Fills the key table: # · Group · What stands out · Records · the job's columns
  function keyTable(table, shown, gv, noteEl) {
    table.replaceChildren();
    if (noteEl) noteEl.textContent = `Numbers match the 3D view. ${gv.riskNote}. Hover any sphere for its label.`;
    if (!shown.length) return;
    const head = table.createTHead().insertRow();
    ["#", "Group", "What stands out", "Records", ...gv.columns.map(([label]) => label)].forEach((h) => {
      const th = document.createElement("th"); th.textContent = h; head.appendChild(th);
    });
    const body = table.createTBody();
    shown.forEach((nd, i) => {
      const tr = body.insertRow();
      const n = tr.insertCell(); n.textContent = `${gv.risky(nd) ? "⚠ " : ""}${i + 1}`;
      if (gv.risky(nd)) n.className = "risk";
      tr.insertCell().textContent = name(nd);
      tr.insertCell().textContent = nd.profile || "–";
      tr.insertCell().textContent = int(nd.size);
      gv.columns.forEach(([, f]) => { tr.insertCell().textContent = f(nd); });
    });
  }

  function hint(g, gv, view = "mapper") {
    const records = g.nodes.reduce((a, nd) => a + nd.size, 0);
    const what = view === "galaxy" ? "The TDA Mapper groups as a galaxy: the same groups and connections, flattened into a disk"
      : "Same layout as CortXplorer's TDA Mapper";
    return `${what} (${int(records)} records; a record can sit in overlapping groups). Size = records, ${VIEWS[view].legend(gv)}. Numbers mark the largest groups and the groups at risk (${gv.riskNote}), explained in the table below.`;
  }

  return { graphView, labelled, traces, layout, keyTable, hint, VIEWS };
})();
