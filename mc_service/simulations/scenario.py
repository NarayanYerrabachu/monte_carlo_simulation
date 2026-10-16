"""Domain-agnostic Monte Carlo on any table and its TDA / ML results.

Input: the records of the dataset CortXplorer analysed (pharma batches, fleet
days, aid projects, …) as a list of numeric *metrics*, an optional *period*
key per record (date, batch run, …), and the TDA cluster ("regime") and ML
anomaly score of every record.

Each simulated period:
  1. if the data has usable periods (≥ 5 periods of ≥ 5 records): draws one
     historical period, so whatever its records share (season, site, market)
     stays together; otherwise samples from all records,
  2. draws ``period_size`` records from it, with replacement,
  3. aggregates every metric (sum or mean of the drawn records) and counts the
     high-risk records (anomaly score ≥ ``high_anomaly``) and the regimes.

Nothing is fitted: the distributions are the data's own. The result describes
its own dashboard (``summary.dashboard``: tiles and charts), so the viewer and
the report render any dataset without knowing its columns.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from mc_service.contract import ScenarioInput, ScenarioMetric
from mc_service.engine import simulate, spawn_seeds
from mc_service.simulations import backtest
from mc_service.simulations.base import RunContext

HIST_BINS = 30
MIN_PERIODS = 5                # fewer periods → sample from all records
MIN_PERIOD_RECORDS = 5         # median records per period below this → sample from all records
DEFAULT_SAMPLE = 100           # records per simulated period when the data has no periods
TAIL = 0.95                    # "high" periods of a metric: above its 95th percentile
MAX_TILES, MAX_CHARTS = 5, 4
VIZ_MAX_POINTS = 3000


def _hist(x: np.ndarray, bins: int = HIST_BINS) -> dict[str, list]:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {"edges": [], "counts": []}
    counts, edges = np.histogram(x, bins=bins)
    return {"edges": np.round(edges, 6).tolist(), "counts": counts.tolist()}


def _q(x: np.ndarray, p: float) -> float | None:
    x = x[np.isfinite(x)]
    return float(np.percentile(x, p)) if x.size else None


def _mean(x: np.ndarray) -> float | None:
    x = x[np.isfinite(x)]
    return float(x.mean()) if x.size else None


def _spread(x: np.ndarray) -> dict[str, float] | None:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    p5, med, p95 = np.percentile(x, [5, 50, 95])
    return {"p5": float(p5), "median": float(med), "p95": float(p95)}


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3 or a[ok].std() == 0 or b[ok].std() == 0:
        return None
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def _fmt(m: ScenarioMetric) -> str:
    """How the viewer formats this metric: pct (rate), int (counts / sums) or num."""
    if m.rate and m.agg == "mean":
        return "pct"
    return "int" if m.agg == "sum" else "num"


def _regime_label(labels: dict[str, str] | None, r: int) -> str:
    return (labels or {}).get(str(r)) or ("Noise / outliers" if r == -1 else f"Cluster {r}")


def _mapper_graph(mapper: dict | None, n: int, M: np.ndarray, score: np.ndarray,
                  metrics: list[ScenarioMetric]) -> tuple[dict | None, dict[int, list[int]]]:
    """The sender's Mapper graph with each group's record statistics and a plain-words
    profile, and the record → groups index used to place a simulated period in the shape."""
    if not mapper or not mapper.get("nodes"):
        return None, {}
    with np.errstate(invalid="ignore"):
        mean, std = np.nanmean(M, axis=0), np.nanstd(M, axis=0)
    nodes, record_nodes = [], {}
    for nd in mapper["nodes"]:
        mem = np.array([r for r in nd.get("members", []) if 0 <= r < n], dtype=int)
        if mem.size == 0:
            continue
        for r in mem:
            record_nodes.setdefault(int(r), []).append(int(nd["id"]))
        with np.errstate(invalid="ignore"):
            mu = np.nanmean(M[mem], axis=0)
        z = np.where(std > 0, (mu - mean) / np.where(std > 0, std, 1), 0.0)
        top = [k for k in np.argsort(-np.abs(np.nan_to_num(z))) if abs(z[k]) >= 0.6][:2]
        profile = " · ".join(f"{'high' if z[k] > 0 else 'low'} {metrics[k].label}" for k in top) or "typical"
        sc = score[mem]
        nodes.append({"id": int(nd["id"]), "x": nd["x"], "y": nd["y"], "z": nd["z"], "size": int(mem.size),
                      "label": nd.get("label"), "short_label": nd.get("short_label"), "profile": profile,
                      "risk": float(np.nanmean(sc)) if np.isfinite(sc).any() else None,
                      "values": {m.key: (None if not np.isfinite(mu[k]) else float(mu[k]))
                                 for k, m in enumerate(metrics)}})
    ids = {nd["id"] for nd in nodes}
    edges = [[int(a), int(b)] for a, b in mapper.get("edges", []) if a in ids and b in ids]
    return {"nodes": nodes, "edges": edges}, record_nodes


def _dashboard(s: ScenarioInput, alerts: list[float | None], has_scores: bool, unit: str) -> dict[str, Any]:
    """Tiles and charts of the live viewer / report, described as data (keys into the
    checkpoints and the per-simulation series)."""
    tiles, charts = [], []
    colors = ["blue", "good", "warm", "bad"]
    shown = s.metrics[: MAX_TILES - 1 if has_scores else MAX_TILES]
    for i, m in enumerate(shown):
        fmt, k = _fmt(m), m.key
        how = "total" if m.agg == "sum" else "average"
        sub = ([{"text": "P(> "}, {"value": alerts[i], "fmt": fmt}, {"text": ") = "}, {"key": f"{k}_p_over", "fmt": "pct"}]
               if alerts[i] is not None else
               [{"text": "90% of " + unit + ": "}, {"key": f"{k}_p5", "fmt": fmt}, {"text": " – "},
                {"key": f"{k}_p95", "fmt": fmt}])
        tiles.append({"label": f"{m.label} ({how} per {s.period_label})", "key": f"{k}_mean", "fmt": fmt,
                      "unit": m.unit, "tone": colors[i % len(colors)], "sub": sub})
    if has_scores:
        tiles.append({"label": "High-risk records", "key": "high_share_mean", "fmt": "pct", "tone": "bad",
                      "sub": [{"text": f"anomaly score ≥ {s.high_anomaly:g} · P95 "}, {"key": "high_p95", "fmt": "int"},
                              {"text": f" per {s.period_label}"}]})
    for i, m in enumerate(s.metrics[: MAX_CHARTS - 1 if has_scores else MAX_CHARTS]):
        fmt, k = _fmt(m), m.key
        lines = [{"key": f"{k}_mean", "label": "expected", "tone": "warm", "fmt": fmt},
                 {"key": f"{k}_p95", "label": "P95", "tone": "bad", "fmt": fmt}]
        if alerts[i] is not None:
            lines.append({"value": alerts[i], "label": "alert", "tone": "blue", "fmt": fmt})
        unit_txt = f" ({m.unit})" if m.unit else ""
        charts.append({"key": k, "series": f"m_{k}", "title": f"{m.label} distribution",
                       "x_title": f"{m.label}{unit_txt} per {s.period_label}", "fmt": fmt, "unit": m.unit,
                       "tone": colors[i % len(colors)], "lines": lines})
    if has_scores:
        charts.append({"key": "high", "series": "high", "title": "High-risk records distribution",
                       "x_title": f"high-risk {s.record_label} per {s.period_label}", "fmt": "int", "tone": "bad",
                       "lines": [{"key": "high_mean", "label": "expected", "tone": "warm", "fmt": "num"},
                                 {"key": "high_p95", "label": "P95", "tone": "bad", "fmt": "int"}]})
    return {"title": s.title or "Scenario simulation", "unit": unit, "period_label": s.period_label,
            "record_label": s.record_label, "tiles": tiles, "charts": charts}


def run(section: ScenarioInput, ctx: RunContext) -> dict:
    s = section
    n, metrics, m = len(s.regime), s.metrics, len(s.metrics)
    M = np.array([[np.nan if v is None else v for v in met.values] for met in metrics], dtype=float).T   # n × m
    valid = np.isfinite(M)
    M = np.where(valid, M, np.nan)
    is_sum = np.array([met.agg == "sum" for met in metrics])
    # a 0/1 column: its per-record average is a rate, shown as a percentage in the tables
    binary = [met.rate or bool(np.isin(M[valid[:, k], k], (0.0, 1.0)).all()) for k, met in enumerate(metrics)]
    regime = np.asarray(s.regime, dtype=int)
    has_scores = s.anomaly_score is not None and any(v is not None for v in s.anomaly_score)
    score = (np.array([np.nan if v is None else v for v in s.anomaly_score], dtype=float)
             if has_scores else np.full(n, np.nan))
    high = np.nan_to_num(score, nan=-np.inf) >= s.high_anomaly

    excluded = np.zeros(n, dtype=bool)
    if s.exclude_anomalies_above is not None:
        excluded = np.nan_to_num(score, nan=-np.inf) >= s.exclude_anomalies_above
        if excluded.all():
            raise ValueError("exclude_anomalies_above removes every record — nothing to simulate")
    pool = ~excluded
    # observed spread per metric before any assumption, for the "observed vs assumed" table
    observed = {met.key: _spread(M[pool, k]) for k, met in enumerate(metrics)}
    # assumed inputs: a shift is added to every record; a range keeps only the records inside it
    applied: list[dict[str, Any]] = []
    for k, met in enumerate(metrics):
        a = (s.assumptions or {}).get(met.key)
        if a is None or (a.low is None and a.high is None and a.shift is None):
            continue
        if a.shift:
            M[:, k] = M[:, k] + a.shift
        inside = np.ones(n, dtype=bool)
        if a.low is not None:
            inside &= ~(np.nan_to_num(M[:, k], nan=a.low) < a.low)
        if a.high is not None:
            inside &= ~(np.nan_to_num(M[:, k], nan=a.high) > a.high)
        removed = int((pool & ~inside).sum())
        pool &= inside
        applied.append({"key": met.key, "label": met.label, "low": a.low, "high": a.high, "shift": a.shift,
                        "records_removed": removed})
    if not pool.any():
        raise ValueError("the assumed ranges leave no record to sample — widen them")
    valid = np.isfinite(M)
    pool_ix = np.flatnonzero(pool)

    # periods (blocks): only when the data really has them
    blocks, block_names = None, []
    if s.period is not None:
        groups: dict[str, list[int]] = {}
        for i in pool_ix:
            if s.period[i] is not None:
                groups.setdefault(str(s.period[i]), []).append(int(i))
        sizes = sorted(len(g) for g in groups.values())
        if len(groups) >= MIN_PERIODS and sizes[len(sizes) // 2] >= MIN_PERIOD_RECORDS:
            block_names = sorted(groups)
            blocks = [np.array(groups[k]) for k in block_names]
    size = s.period_size or (int(np.median([b.size for b in blocks])) if blocks else min(int(pool.sum()), DEFAULT_SAMPLE))
    unit = f"{s.period_label}s"

    def aggregate(tot: np.ndarray, cnt: np.ndarray, k: int) -> np.ndarray:
        """Metric values of a draw of ``k`` records: sums are scaled to the period size when
        some records miss the value; means are over the records that have it."""
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(cnt > 0, tot / cnt, np.nan)
        return np.where(is_sum, mean * k, mean)

    reg_ids = sorted(set(regime[pool].tolist()))
    reg_pos = np.zeros(n, dtype=int)
    for j, r in enumerate(reg_ids):
        reg_pos[regime == r] = j
    R = len(reg_ids)
    stats = np.column_stack([np.where(valid, M, 0.0), valid.astype(float), high.astype(float)])   # n × (2m + 1)

    # alert thresholds: sent by the caller, else the P90 of the historical periods (none without periods)
    alerts: list[float | None] = []
    hist_periods = None
    if blocks:
        hist_periods = np.array([aggregate(stats[b, :m].sum(axis=0), stats[b, m:2 * m].sum(axis=0), size) for b in blocks])
    for k, met in enumerate(metrics):
        if met.alert is not None:
            alerts.append(float(met.alert))
        elif hist_periods is not None and np.isfinite(hist_periods[:, k]).any():
            alerts.append(float(np.nanpercentile(hist_periods[:, k], 90)))
        else:
            alerts.append(None)

    def pick_records(rng: np.random.Generator) -> tuple[int | None, np.ndarray]:
        b = int(rng.integers(len(blocks))) if blocks else None
        return b, rng.choice(blocks[b] if blocks else pool_ix, size=size)

    def draw(rng: np.random.Generator) -> np.ndarray:
        """One simulated period: metric values, high-risk count, records per regime, period drawn."""
        b, pick = pick_records(rng)
        tot = stats[pick].sum(axis=0)
        return np.concatenate([aggregate(tot[:m], tot[m:2 * m], size), [tot[2 * m]],
                               np.bincount(reg_pos[pick], minlength=R), [-1 if b is None else b]])

    # ── live viewer ─────────────────────────────────────────────────────────
    graph, record_nodes = _mapper_graph(s.mapper, n, M, score, metrics)
    if graph:
        # colour scale up to the riskiest group (at least 0.5), so differences between groups show
        top = max((nd["risk"] or 0.0 for nd in graph["nodes"]), default=0.0)
        graph["view"] = {
            "color_key": "risk", "color_title": "anomaly score", "risk_above": 0.4,
            "color_max": max(0.5, float(np.ceil(top * 10) / 10)),
            "unit": s.record_label, "period_label": s.period_label,
            "columns": [{"key": "risk", "label": "Avg anomaly", "fmt": "num"},
                        *({"key": f"values.{met.key}", "label": met.label, "fmt": "pct" if binary[k] else "num"}
                          for k, met in enumerate(metrics[:2]))]}
    dashboard = _dashboard(s, alerts, has_scores, unit)
    viz = np.arange(n) if n <= VIZ_MAX_POINTS else np.sort(
        np.random.default_rng(ctx.settings.seed).choice(n, VIZ_MAX_POINTS, replace=False))
    viz_pos = {int(r): i for i, r in enumerate(viz)}
    axes = np.nan_to_num(np.column_stack([M[:, min(k, m - 1)] for k in range(3)])[viz])
    labels = {str(r): _regime_label(s.regime_labels, r) for r in sorted(set(regime.tolist()))}
    ctx.live.set_observed(
        stat=None, alpha=ctx.settings.alpha, mode="none", graph=graph, dashboard=dashboard,
        points=np.round(axes, 4), groups=regime[viz], group_labels=labels,
        axis_titles=[metrics[min(k, m - 1)].label for k in range(3)],
        statistic=f"high-risk share of the simulated {s.period_label}", headline_label="Records per " + s.period_label,
        headline=size)
    seeds = spawn_seeds(ctx.settings.seed, ctx.settings.n_sims)
    done: list[np.ndarray] = []

    def kpis(a: np.ndarray) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(a)}
        for k, met in enumerate(metrics):
            col = a[:, k]
            out[f"{met.key}_mean"] = _mean(col)
            out[f"{met.key}_p5"], out[f"{met.key}_p50"], out[f"{met.key}_p95"] = _q(col, 5), _q(col, 50), _q(col, 95)
            out[f"{met.key}_alert"] = alerts[k]
            out[f"{met.key}_p_over"] = (float(np.nanmean(col > alerts[k])) if alerts[k] is not None else None)
        hi = a[:, m]
        out.update(high_mean=float(hi.mean()), high_p95=_q(hi, 95), high_share_mean=float(hi.mean() / size),
                   p_any_high=float((hi > 0).mean()))
        return out

    def on_chunk(lo: int, draws: np.ndarray) -> None:
        ctx.live.add_series({**{f"m_{met.key}": np.round(draws[:, k], 6).tolist() for k, met in enumerate(metrics)},
                             "high": draws[:, m].tolist()})
        ctx.live.add_null(np.round(draws[:, m] / size, 6).tolist())
        done.append(draws)
        ctx.live.add_checkpoint(kpis(np.concatenate(done)))
        i = lo + len(draws) - 1                         # rebuild the period simulation i drew
        b, pick = pick_records(np.random.default_rng(seeds[i]))
        nodes = Counter(k for r in pick for k in record_nodes.get(int(r), ()))
        ctx.live.set_frame(index=i, n=i + 1, day=block_names[b] if b is not None else f"sample {i + 1}",
                           highlight=sorted({viz_pos[int(r)] for r in pick if int(r) in viz_pos}),
                           nodes=[[k, c] for k, c in sorted(nodes.items())])

    res = simulate(draw, ctx.sim_config(chunk_size=max(1, ctx.settings.n_sims // 100), on_chunk=on_chunk))
    sim = res.null.reshape(-1, m + 2 + R) if res.n_completed else np.zeros((0, m + 2 + R))
    kpi = kpis(sim) if res.n_completed else {"n": 0}
    kpi["period_size"] = size

    # convergence of the first metric's expected value (running mean ± 1.96 standard errors)
    convergence: dict[str, list] = {"n": [], "p": [], "lo": [], "hi": [], "label": f"expected {metrics[0].label}"}
    first = sim[:, 0] if res.n_completed else np.array([])
    if first.size and np.isfinite(first).all():
        k = np.arange(1, first.size + 1)
        run_mean = np.cumsum(first) / k
        var = np.maximum(np.cumsum(first ** 2) / k - run_mean ** 2, 0)
        se = np.sqrt(var / k)
        pts = np.unique(np.round(np.geomspace(min(50, first.size), first.size, 40)).astype(int))
        convergence.update(n=list(map(int, pts)), p=[float(run_mean[i - 1]) for i in pts],
                           lo=[float(run_mean[i - 1] - 1.96 * se[i - 1]) for i in pts],
                           hi=[float(run_mean[i - 1] + 1.96 * se[i - 1]) for i in pts])

    # regimes: observed profile on the sampling pool, and how over-represented each regime is
    # in the periods where a metric is high (above its 95th percentile)
    reg_counts = sim[:, m + 1:m + 1 + R]
    tails = []
    for k, met in enumerate(metrics[:3]):
        col = sim[:, k]
        if not np.isfinite(col).any():
            continue
        top = col >= np.nanpercentile(col, TAIL * 100)
        base = reg_counts.mean(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            lift = np.where(base > 0, reg_counts[top].mean(axis=0) / base, np.nan) if top.any() else np.full(R, np.nan)
        tails.append({"key": met.key, "label": met.label, "lift": lift})
    regimes = []
    for j, r in enumerate(reg_ids):
        mask = pool & (regime == r)
        with np.errstate(invalid="ignore"):
            mu = np.nanmean(M[mask], axis=0)
        regimes.append({
            "regime": r, "label": _regime_label(s.regime_labels, r), "records": int(mask.sum()),
            "share": float(mask.sum() / pool.sum()),
            "anomaly_mean": float(np.nanmean(score[mask])) if np.isfinite(score[mask]).any() else None,
            "high_risk_share": float(high[mask].mean()) if has_scores else None,
            "metrics": {met.key: (float(mu[k]) if np.isfinite(mu[k]) else None) for k, met in enumerate(metrics)},
            "tail_lift": {t["key"]: (float(t["lift"][j]) if np.isfinite(t["lift"][j]) else None) for t in tails},
        })

    # backtest: simulate from the first 80 % of the periods, compare with the last 20 %
    if blocks:
        high_obs = np.array([stats[b, 2 * m].sum() * size / b.size for b in blocks])
        bt = backtest.run(sim[:, -1], block_names, [
            *({"key": met.key, "label": met.label, "fmt": _fmt(met), "unit": met.unit,
               "sim": sim[:, k], "observed": hist_periods[:, k]} for k, met in enumerate(metrics)),
            *([{"key": "high", "label": "High-risk records", "fmt": "num", "unit": None,
                "sim": sim[:, m], "observed": high_obs}] if has_scores else [])], unit)
    else:
        bt = backtest.unavailable("the data has no usable periods, so there is no time order to hold out")

    described = [{"key": met.key, "label": met.label, "agg": met.agg, "unit": met.unit, "fmt": _fmt(met),
                  "record_fmt": "pct" if binary[k] else "num", "alert": alerts[k]} for k, met in enumerate(metrics)]

    # what drives the risk: how each metric's period value moves with the number of alerts
    # breached in that period and with its high-risk records (correlation over the scenarios)
    n_alerts = np.zeros(len(sim))
    for k, met in enumerate(metrics):
        if alerts[k] is not None and len(sim):
            n_alerts += np.nan_to_num(sim[:, k] > alerts[k]).astype(float)
    sensitivity = [{"key": met.key, "label": met.label,
                    "alerts": _corr(sim[:, k], n_alerts) if len(sim) and any(a is not None for a in alerts) else None,
                    "high": _corr(sim[:, k], sim[:, m]) if len(sim) and has_scores else None}
                   for k, met in enumerate(metrics)]
    narrative = _narrative(s, described, kpi, regimes, tails, sensitivity, applied, observed, bt, convergence,
                           size, int(pool.sum()), n, has_scores, unit) if res.n_completed else {}
    return {
        "test": "scenario",
        "n_completed": res.n_completed, "stopped_early": res.stopped_early, "elapsed_s": res.elapsed_s,
        "config": {"title": s.title, "period_size": size, "period_label": s.period_label, "record_label": s.record_label,
                   "high_anomaly": s.high_anomaly, "exclude_anomalies_above": s.exclude_anomalies_above,
                   "assumptions": {a["key"]: {"low": a["low"], "high": a["high"], "shift": a["shift"]} for a in applied},
                   "n_records": n, "n_pool": int(pool.sum()), "n_periods": len(blocks) if blocks else None,
                   "n_sims": ctx.settings.n_sims, "seed": ctx.settings.seed,
                   "method": (f"{s.period_label}-block bootstrap of the records" if blocks
                              else "bootstrap of the records (the data has no usable periods)")},
        "results": regimes,
        "summary": {
            "kpi": kpi, "metrics": described, "dashboard": dashboard,
            "hist": {**{met.key: _hist(sim[:, k]) for k, met in enumerate(metrics)},
                     "high": _hist(sim[:, m], bins=int(max(1, min(HIST_BINS, sim[:, m].max() - sim[:, m].min() + 1)))
                                   if res.n_completed else 1)},
            "tails": [{"key": t["key"], "label": t["label"]} for t in tails],
            "anomalies": {"threshold": s.exclude_anomalies_above, "excluded": int(excluded.sum()),
                          "high_risk": int(high.sum()),
                          "record_ids": [s.record_id[i] for i in np.flatnonzero(excluded)[:50]] if s.record_id else []},
            "convergence": convergence, "backtest": bt,
            "inputs": {"records": n, "periods": len(blocks) if blocks else None, "period_size": size,
                       "metrics": {met.key: _spread(M[pool, k]) for k, met in enumerate(metrics)},
                       "observed": observed, "assumptions": applied},
            "sensitivity": sensitivity,
            **narrative,
            "context": s.context,
        },
    }


def _val(v: float | None, fmt: str, unit: str | None) -> str:
    if v is None:
        return "–"
    if fmt == "pct":
        return f"{100 * v:.1f}%"
    a = abs(v)
    txt = (f"{v:,.0f}" if fmt == "int" or a >= 1000 else f"{v:,.2f}" if a >= 1 else f"{v:,.3f}" if a >= 0.1
           else f"{v:,.4f}" if a > 0 else "0")                    # small values keep their digits (LogReturn 0.0004)
    return f"{txt} {unit}".strip() if unit else txt


def _narrative(s: ScenarioInput, metrics: list[dict], k: dict, regimes: list[dict], tails: list[dict],
               sensitivity: list[dict], applied: list[dict], observed: dict, bt: dict, cv: dict,
               size: int, n_pool: int, n: int, has_scores: bool, unit: str) -> dict[str, Any]:
    """Key results, section texts and recommendations in plain words, all computed from the numbers
    (the same for the page, the PDF and the Excel file)."""
    period, records = s.period_label, s.record_label
    findings = []
    for m in metrics[:4]:
        key, fmt, u = m["key"], m["fmt"], m.get("unit")
        how = "total" if m["agg"] == "sum" else "average"
        sub = (f"P(> {_val(m['alert'], fmt, u)}) = {_val(k.get(f'{key}_p_over'), 'pct', None)}" if m.get("alert") is not None
               else f"90% of {unit} between {_val(k.get(f'{key}_p5'), fmt, u)} and {_val(k.get(f'{key}_p95'), fmt, u)}")
        findings.append({"value": _val(k.get(f"{key}_mean"), fmt, u), "label": f"{m['label']} ({how} per {period})", "sub": sub})
    if has_scores:
        findings.append({"value": _val(k.get("p_any_high"), "pct", None), "label": f"{period.capitalize()}s with a high-risk record",
                         "sub": f"expected {k.get('high_mean', 0):.1f} high-risk {records} per {period}, P95 {_val(k.get('high_p95'), 'int', None)}"})
    # the metric most often over its alert, the regime most over-represented in the tails
    worst = max((m for m in metrics if m.get("alert") is not None and k.get(f"{m['key']}_p_over") is not None),
                key=lambda m: k[f"{m['key']}_p_over"], default=None)
    top_regime, top_lift, top_tail = None, 0.0, None
    for g in regimes:
        for t in tails:
            lift = g["tail_lift"].get(t["key"])
            if lift is not None and lift > top_lift and g["share"] >= 0.02:
                top_regime, top_lift, top_tail = g, lift, t
    drivers = sorted((d for d in sensitivity if d.get("alerts") is not None or d.get("high") is not None),
                     key=lambda d: -max(abs(d.get("alerts") or 0), abs(d.get("high") or 0)))
    text = {
        "inputs": (f"Every simulated {period} resamples {size:,} {records} from the {n_pool:,} in the sampling pool"
                   + (f" ({n:,} in the data; {n - n_pool:,} left out by the assumptions and the anomaly filter)" if n_pool < n else "")
                   + ". The inputs are the data's own values: nothing is fitted."
                   + (" Assumed ranges and shifts: " + "; ".join(
                       f"{a['label']}" + (f" between {a['low']:g} and {a['high']:g}" if a["low"] is not None and a["high"] is not None
                                          else f" at least {a['low']:g}" if a["low"] is not None
                                          else f" at most {a['high']:g}" if a["high"] is not None else "")
                       + (f", shifted by {a['shift']:+g}" if a["shift"] else "")
                       + (f" ({a['records_removed']:,} {records} outside)" if a["records_removed"] else "")
                       for a in applied) + "." if applied else "")),
        "results": " ".join(f"{f['label']}: {f['value']} ({f['sub']})." for f in findings),
        "drivers": (((f"An alert is a {s.period_label} in which an input ends above its alert level: the level in the "
                      f"what-if table, the 90th percentile of its own history unless one was set. "
                      if any(d.get("alerts") is not None for d in drivers) else
                      f"A high-risk {records} is one the TDA / ML scoring flags as unusual. ") if drivers else "")
                    + (f"{drivers[0]['label']} moves most with the risk (correlation "
                     f"{(drivers[0].get('alerts') if drivers[0].get('alerts') is not None else drivers[0].get('high')):+.2f}); "
                     + ("; ".join(f"{d['label']} {(d.get('alerts') if d.get('alerts') is not None else d.get('high')):+.2f}" for d in drivers[1:3])
                        + "." if len(drivers) > 1 else "")) if drivers else "No alert thresholds and no anomaly scores: nothing to correlate the inputs with.")
                   + (f" Regime '{top_regime['label']}' ({top_regime['share']:.0%} of the {records}) appears ×{top_lift:.1f} as often in the "
                      f"{unit} with the highest {top_tail['label']}." if top_regime else ""),
        "quality": (f"After {k.get('n', 0):,} scenarios the estimate of {cv.get('label', 'the first metric')} is "
                    f"{_val(cv['p'][-1], metrics[0]['fmt'], metrics[0].get('unit'))} with a 95% interval of "
                    f"{_val(cv['lo'][-1], metrics[0]['fmt'], metrics[0].get('unit'))} to {_val(cv['hi'][-1], metrics[0]['fmt'], metrics[0].get('unit'))}: "
                    "more scenarios would not change the conclusions; what remains uncertain is the data and the assumptions."
                    if cv.get("p") else "") + (f" Backtest: {bt.get('verdict', '')}." if bt and bt.get("verdict") else ""),
    }
    recs = []
    if worst is not None and k[f"{worst['key']}_p_over"] >= 0.2:
        recs.append({"title": f"Plan for {worst['label']} above its alert",
                     "text": (f"In {_val(k[f'{worst['key']}_p_over'], 'pct', None)} of simulated {unit} {worst['label']} exceeds "
                              f"{_val(worst['alert'], worst['fmt'], worst.get('unit'))}. A plan built on the expected value "
                              f"({_val(k.get(f'{worst['key']}_mean'), worst['fmt'], worst.get('unit'))}) falls short that often; "
                              f"size reserves at the 95th percentile, {_val(k.get(f'{worst['key']}_p95'), worst['fmt'], worst.get('unit'))}.")})
    elif metrics:
        m0 = metrics[0]
        recs.append({"title": f"Set an alert for {m0['label']}",
                     "text": (f"No alert is breached often. A threshold at the 95th percentile, "
                              f"{_val(k.get(f'{m0['key']}_p95'), m0['fmt'], m0.get('unit'))} per {period}, flags the one in twenty "
                              f"{unit} that deserve a look; edit it in the what-if box.")})
    if top_regime is not None and top_lift >= 1.5:
        recs.append({"title": f"Watch regime '{top_regime['label']}'",
                     "text": (f"It holds {top_regime['share']:.0%} of the {records} but appears ×{top_lift:.1f} as often in the {unit} "
                              f"with the highest {top_tail['label']}. The TDA regime is a leading indicator: {records} that fall into it "
                              "deserve a check before the period closes.")})
    if drivers and drivers[0].get("alerts") is not None and abs(drivers[0]["alerts"]) >= 0.3 and len(metrics) > 1:
        recs.append({"title": f"Act on {drivers[0]['label']} first",
                     "text": (f"Of all inputs it moves most with the number of alerts in a {period} (correlation {drivers[0]['alerts']:+.2f}). "
                              f"Changing it by its own spread ({_val((observed.get(drivers[0]['key']) or {}).get('p5'), 'num', None)} to "
                              f"{_val((observed.get(drivers[0]['key']) or {}).get('p95'), 'num', None)} per record) changes the outcome more than "
                              "the other inputs do; try it with a shift in the what-if box.")})
    if has_scores and (k.get("p_any_high") or 0) >= 0.5:
        recs.append({"title": "Inspect the high-risk records before trusting the tails",
                     "text": (f"{_val(k.get('p_any_high'), 'pct', None)} of {unit} contain a record the anomaly model scores ≥ {s.high_anomaly:g}. "
                              "If those are data errors, switch the exclusion on and re-run; if they are real events, the tails above are the risk.")})
    if applied:
        recs.append({"title": "Read the results as conditional",
                     "text": (f"The assumed ranges keep {n_pool:,} of {n:,} {records}. The results hold if the inputs really stay inside them; "
                              "re-run without the assumptions to see the unconditional picture.")})
    if bt and bt.get("verdict") == "does not hold":
        recs.append({"title": "Treat the ranges as a floor",
                     "text": "The backtest shows the newest periods moved beyond what the earlier ones would have predicted; "
                             "the data is drifting, so the simulated spread understates the risk ahead."})
    return {"findings": findings, "text": text, "recommendations": recs[:5],
            "predictions": _predictions(s, metrics, k, bt, has_scores, unit)}


def _predictions(s: ScenarioInput, metrics: list[dict], k: dict, bt: dict, has_scores: bool, unit: str) -> dict[str, Any]:
    """What the simulation says about the next period(s): expected value, likely range, chance
    of breaching the alert in the next one and in the next 5 / 20 periods (independent periods),
    and whether the most recent periods (the backtest's held-out part) ran above or below it."""
    period = s.period_label
    recent = {r["key"]: r for r in (bt or {}).get("metrics") or []} if bt and bt.get("available", True) else {}
    rows = []
    for m in metrics:
        key, fmt, u = m["key"], m["fmt"], m.get("unit")
        p = k.get(f"{key}_p_over")
        p5, p95, mean = k.get(f"{key}_p5"), k.get(f"{key}_p95"), k.get(f"{key}_mean")
        r = recent.get(key) or {}
        obs, sim_mean = r.get("observed_mean"), r.get("sim_mean")
        drift = None
        if obs is not None and sim_mean is not None and p5 is not None and p95 is not None and p95 > p5:
            rel = (obs - sim_mean) / (p95 - p5)
            drift = "above" if rel > 0.25 else "below" if rel < -0.25 else "in line"
        outlook = ("no alert set" if p is None else
                   "very likely stays below the alert" if p < 0.05 else
                   "usually below the alert" if p < 0.2 else
                   "breaches the alert often" if p < 0.5 else "breaches the alert more often than not")
        rows.append({"key": key, "label": m["label"], "unit": u, "fmt": fmt, "agg": m["agg"],
                     "expected": mean, "low": p5, "high": p95, "alert": m.get("alert"), "p_over": p,
                     "p_over_5": (1 - (1 - p) ** 5) if p is not None else None,
                     "p_over_20": (1 - (1 - p) ** 20) if p is not None else None,
                     "recent": obs, "drift": drift, "outlook": outlook})
    high = None
    if has_scores:
        high = {"expected": k.get("high_mean"), "p95": k.get("high_p95"), "p_any": k.get("p_any_high"),
                "p_any_5": (1 - (1 - k["p_any_high"]) ** 5) if k.get("p_any_high") is not None else None}
    worst = max((r for r in rows if r["p_over"] is not None), key=lambda r: r["p_over"], default=None)
    parts = []
    if worst:
        parts.append(f"For the next {period}, {worst['label']} is the metric most likely to breach its alert: "
                     f"{_val(worst['p_over'], 'pct', None)} for one {period}, {_val(worst['p_over_20'], 'pct', None)} for at least one of the next 20.")
    drifting = [r for r in rows if r["drift"] in ("above", "below")]
    if drifting:
        parts.append("The most recent " + unit + " ran " + "; ".join(f"{r['label']} {r['drift']} the simulated range" for r in drifting[:3])
                     + ": read those predictions as a floor, the data is moving.")
    elif recent:
        parts.append(f"The most recent {unit} ran in line with the simulation, so the ranges below are a fair forecast.")
    if high:
        parts.append(f"Expect about {high['expected']:.1f} high-risk {s.record_label} in the next {period} "
                     f"(up to {_val(high['p95'], 'int', None)} on a bad one); the chance of at least one is {_val(high['p_any'], 'pct', None)}.")
    return {"horizon": period, "rows": rows, "high": high, "text": " ".join(parts),
            "note": (f"Expected values and ranges come from the {k.get('n', 0):,} simulated {unit}. Multi-{period} chances treat {unit} as independent "
                     f"draws from the same history: a simplification that understates clustered risk.")}
