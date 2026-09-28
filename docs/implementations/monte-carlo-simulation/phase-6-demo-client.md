# Phase 6: Demo Client & Endpoints

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: CorteXplorer_tda_demo

## Goal
The demo builds a `SimulationRequest` from its current state, sends it to the Monte Carlo service,
tracks the job and serves the results under `/api/monte-carlo/*`.

## End-of-Phase System State
- `POST /api/monte-carlo/run {tests: [...]}` submits a job; `GET /status`, `GET /results` proxy it.
- `docker compose up` starts demo + service; `MC_SERVICE_URL` wires them.
- Without `MC_SERVICE_URL` the demo behaves exactly as before; the endpoints return 503.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 6.1 | `backend/services/monte_carlo_client.py` | High | ⏸️ | `build_request(state, tests)` + `submit/status/result/cancel` (httpx, gzip) |
| 6.2 | `backend/api/monte_carlo.py`, `backend/main.py` | Medium | ⏸️ | Router (EnvelopeRoute), `dataset_id` per rebuild, cache in `_state["_monte_carlo"]` |
| 6.3 | `backend/main.py` (`_rebuild_state_from_df`) | Low | ⏸️ | Evict cache + cancel running job on rebuild |
| 6.4 | `backend/services/pre_event.py` | Medium | ⏸️ | Expose event rows / band inputs (`pre_event_inputs(...)`); no behaviour change |
| 6.5 | `backend/tda/engine.py` | Low | ⏸️ | Keep PCA projection + Mapper eps actually used available for the payload |
| 6.6 | `docker-compose.yml`, `.env.example` | Low | ⏸️ | `monte-carlo` service, `MC_*` variables |
| 6.7 | `tests/scripts/export_mc_fixtures.py` | Medium | ⏸️ | Export parity fixtures from `_make_mock_df` into the service repo |

## Detailed Task Descriptions

### Task 6.1 – Payload builder
Snapshot under `_lock`, then build outside it:
- `loops.X` = PCA(≤5) projection used by `compute_persistent_homology` (all rows; service samples).
- `relationships` = Era/Regime (or detected A/B) labels, `_pre_event_mapper` membership, ticker blocks +
  date order when dated.
- `pre_event` = timeline, event rows for the selected regime/year, standardised Z, anomaly scores,
  event-region mask, bands.
- `anomaly_stability` = `X_norm`, record ids, cluster labels, contamination, weights, thresholds, top-K.
- `mapper_stability` = `X_norm`, lens values, parameters actually used.
Only sections for requested tests are built. Tests not applicable to the dataset (e.g. pre-event on
undated data) are rejected with a clear 400 message.

**Errors:** `MC_SERVICE_URL` unset → 503 "Monte Carlo service not configured"; connect/timeout/5xx
→ 502 with the service message; never empty results.

### Task 6.2 – Endpoints
- `POST /api/monte-carlo/run` → `{job_id, tests}`; 409 if a job for this dataset is running.
- `GET /api/monte-carlo/status` → service status (or `{status: "idle"}`).
- `GET /api/monte-carlo/results` → cached `SimulationResponse`, fetched once when done; dropped if
  `dataset_id` ≠ current.
Guard with `state_service.require_pipeline()`.

**DoD:**
- [ ] All endpoints in the `{status, data, message}` envelope
- [ ] Rebuild during a job ⇒ job cancelled, cache empty, stale result never shown
- [ ] Demo test suite unchanged when the service is absent

**Gotchas:**
- Do not import from `main.py` in the router (circular) — use `services/state.py`.
- Payload for 25k rows: build with numpy `.tolist()`, gzip; log the size.

## Git Commit
`[impl] Monte Carlo: demo client, proxy endpoints and compose service`
