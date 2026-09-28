# Phase 8: Tests & Quality Gate

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: both

## Goal
Prove the statistics are calibrated, the service matches the demo, and the integration is robust.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 8.1 | service `tests/test_calibration.py` | Medium | ⏸️ | Under true nulls: p ~ U(0,1) (KS, fixed seed); FPR ≤ α + tol for every test |
| 8.2 | service `tests/test_parity.py` | Medium | ⏸️ | Lift, band metrics, anomaly score, Mapper membership == demo fixtures |
| 8.3 | service `tests/test_determinism.py` | Low | ⏸️ | Same request ⇒ identical response; `MC_N_JOBS` 1 vs 4 identical |
| 8.4 | demo `tests/test_monte_carlo.py` | Medium | ⏸️ | `httpx.MockTransport`: 503 unset, 502 down, stale `dataset_id`, eviction + cancel, envelope |
| 8.5 | `tests/e2e` (both, compose) | Medium | ⏸️ | Both containers, mock dataset, every test end-to-end |
| 8.6 | docs | Low | ⏸️ | READMEs, `docs/architecture.md`, `.env.example`, demo project skill |

**DoD:**
- [ ] Service suite 100 % pass; demo suite ≥ baseline (110 passed / 6 skipped) + new tests pass
- [ ] Calibration tests use modest n (fast) and are marked so CI stays < 2 min
- [ ] Gov-aid 25k run within `MC_MAX_SECONDS` at defaults (manual, timings recorded here)

## Git Commit
`[test] Monte Carlo: calibration, parity and integration tests`
