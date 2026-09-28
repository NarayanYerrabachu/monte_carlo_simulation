# Phase 4: Pre-Event Pseudo-Event Test

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: monte_carlo_simulation

## Goal
For each pre-event band (e.g. 6–4, 4–2, 2–0 weeks before a Crash) and each metric, a p-value against
bands placed before randomly chosen pseudo-events.

## End-of-Phase System State
- A job with a `pre_event` section returns observed band metrics, null quantiles and p-values.
- Observed band metrics equal the demo's `pre_event_patterns` on committed fixtures.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 4.1 | `mc_service/simulations/pre_event.py` | Medium | ⏸️ | `band_stats(event_rows, …)` — same metrics as the demo |
| 4.2 | `mc_service/simulations/pre_event.py` | Medium | ⏸️ | `sample_pseudo_events` + null + p-values |
| 4.3 | `tests/test_pre_event.py` | Medium | ⏸️ | Planted pre-event shift, no-shift, parity |

## Detailed Task Descriptions

### Task 4.1 – Band statistics
For band `(hi, lo)` weeks: rows at `pos_in_ticker` in `[k − hi·days, k − lo·days)` before each event row
`k` in the same ticker, event day excluded (demo rule). Metrics per band:
`z_mean[feature]` (mean of `Z`), `anomaly_mean`, `share_high` (anomaly ≥ `high_threshold`),
`event_region_share` (if `event_region` given).

### Task 4.2 – Null
- Pseudo-event set: for each ticker, as many events as that ticker really has, drawn uniformly from
  rows with `pos_in_ticker ≥ max_band_weeks · days_per_week` (enough history), excluding real event rows.
- Per simulation compute the same band metrics; two-sided p per (band, metric).
- BH across all (band, metric) cells → q.

**Result fields:** `results = [{band, metric, observed, null_mean, null_ci95, p_value, q_value,
significant}]`, `summary = {n_events, n_tickers, n_significant}`.

**DoD:**
- [ ] Planted shift (feature +1 σ in the 2 weeks before events) ⇒ that cell significant
- [ ] No shift ⇒ false-positive rate ≈ α across cells (calibration)
- [ ] Parity with demo band metrics on fixture

**Gotchas:**
- Demo phase 6 must refactor `pre_event_patterns` so the event rows it uses are exportable (today
  `find_events` is internal). The service receives `event_rows`; it does not re-detect events.
- Tickers with fewer eligible rows than events: sample without replacement up to what exists; record
  `n_events_skipped` in the summary.

## Git Commit
`[impl] Monte Carlo service: pre-event pseudo-event test`
