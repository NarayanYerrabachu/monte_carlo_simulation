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

    def spread(x: np.ndarray) -> dict[str, float] | None:
        x = x[np.isfinite(x)]
        if x.size == 0:
            return None
        p5, med, p95 = np.percentile(x, [5, 50, 95])
        return {"p5": float(p5), "median": float(med), "p95": float(p95)}

    described = [{"key": met.key, "label": met.label, "agg": met.agg, "unit": met.unit, "fmt": _fmt(met),
                  "record_fmt": "pct" if binary[k] else "num", "alert": alerts[k]} for k, met in enumerate(metrics)]
    return {
        "test": "scenario",
        "n_completed": res.n_completed, "stopped_early": res.stopped_early, "elapsed_s": res.elapsed_s,
        "config": {"title": s.title, "period_size": size, "period_label": s.period_label, "record_label": s.record_label,
                   "high_anomaly": s.high_anomaly, "exclude_anomalies_above": s.exclude_anomalies_above,
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
                       "metrics": {met.key: spread(M[pool, k]) for k, met in enumerate(metrics)}},
            "context": s.context,
        },
    }
