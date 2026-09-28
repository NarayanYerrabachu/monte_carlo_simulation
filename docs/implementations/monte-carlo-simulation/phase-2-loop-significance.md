# Phase 2: Loop (H1) Significance

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: monte_carlo_simulation

## Goal
Tell which persistent-homology loops are more persistent than loops in structureless surrogate data.

## End-of-Phase System State
- A job with a `loops` section returns the observed H1 diagram (recomputed on the sample), the Monte
  Carlo noise band, a p-value and `significant` flag per loop, and the demo's heuristic threshold for
  comparison.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 2.1 | `mc_service/simulations/loops.py` | Medium | ⏸️ | Observed diagram on a seeded sample of `X` (same `sample_n` as the nulls) |
| 2.2 | `mc_service/simulations/loops.py` | Medium | ⏸️ | Null: surrogate → same sample size → ripser maxdim 1 → max H1 persistence |
| 2.3 | `mc_service/simulations/loops.py`, `mc_service/api/jobs.py` | Low | ⏸️ | Result model + wiring into the job runner |
| 2.4 | `tests/test_loops.py` | Medium | ⏸️ | Planted circle vs Gaussian blob, determinism |

## Detailed Task Descriptions

### Task 2.1/2.2 – Statistic
- Observed: sample `sample_n` rows of `X` (the demo's PCA projection) with the request seed; ripser
  maxdim 1; H1 persistences `p_1 ≥ p_2 ≥ …`.
- Null draw: surrogate of the **full** `X` (`gaussian` or `shuffle`) → sample `sample_n` → ripser →
  `max H1 persistence` (0 if no H1 bar).
- Noise band = 95th percentile (1 − α) of the null maxima.
- Per loop: `p = (1 + #{null_max ≥ p_i}) / (1 + n)`, `significant = p < alpha`. Family-wise by design.

**Result fields:** `results = [{index, birth, death, persistence, p_value, significant}]` (top 50 by
persistence), `summary = {noise_band, heuristic_threshold, n_loops_observed, n_significant, null_model,
sample_n, null_max_quantiles: {p50, p90, p95, p99}, null_hist: {edges, counts}}` (histogram for the UI).

**DoD:**
- [ ] Noisy circle (n=1000, σ=0.1) ⇒ top loop `significant`, all others not
- [ ] Gaussian blob and correlated ellipse ⇒ `n_significant = 0` (with `gaussian` null)
- [ ] Same seed ⇒ identical result; progress reported per simulation

**Gotchas:**
- Observed and null must use the **same sample size** — persistence scales with sampling density.
- The demo computes its diagram on a 3000-point sample; the service's observed diagram is on
  `sample_n` points, so loop indices do not map 1:1 to the demo's. The demo shows the service diagram's
  significant count and the noise band on its own diagram, not per-bar p-values on its bars.
- ripser time grows fast with n; keep `sample_n ≤ 1500` in the contract validator.

## Git Commit
`[impl] Monte Carlo service: loop (H1) significance test`
