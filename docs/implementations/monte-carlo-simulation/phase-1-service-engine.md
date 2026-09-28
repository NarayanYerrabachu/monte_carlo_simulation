# Phase 1: Service Skeleton, Contract & Engine

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: monte_carlo_simulation

## Goal
A running FastAPI service on port 8020 that accepts a `SimulationRequest`, runs it as a background job
and returns a `SimulationResponse`. No real tests yet — a built-in `echo` null (sample means of random
normals) proves the job pipeline and engine end-to-end.

## End-of-Phase System State
- `pipenv run uvicorn mc_service.main:app --port 8020` starts; `GET /health` returns the envelope.
- `POST /v1/jobs` validates the contract, returns 202 + `job_id`; status / result / cancel work.
- `simulate()` is deterministic for any `MC_N_JOBS`; `p_value`, `bh_qvalues`, `percentile_ci` tested.
- Docker image builds; `pipenv run pytest -q` passes.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 1.1 | `Pipfile`, `.python-version`, `.gitignore`, `Dockerfile`, `docker-compose.yml`, `README.md` | Low | ⏸️ | Project skeleton (Python 3.13, pipenv venv name `montecarlo`) |
| 1.2 | `mc_service/main.py`, `mc_service/api/envelope.py`, `mc_service/api/jobs.py` | Medium | ⏸️ | App, envelope route class (same shape as demo), job endpoints, gzip request bodies, body limit |
| 1.3 | `mc_service/contract.py` | Medium | ⏸️ | Pydantic models `contract_version "1"`, cross-field validation |
| 1.4 | `mc_service/jobs.py` | Medium | ⏸️ | In-memory job store, `ThreadPoolExecutor(MC_WORKERS)`, progress, cancel flag, TTL cleanup |
| 1.5 | `mc_service/engine.py`, `mc_service/nulls.py` | Medium | ⏸️ | `SimConfig`, `SimResult`, `simulate`, `p_value`, `bh_qvalues`, `percentile_ci`; null generators |
| 1.6 | `tests/test_engine.py`, `tests/test_api.py` | Medium | ⏸️ | Unit + TestClient tests |

## Detailed Task Descriptions

### Task 1.2 – App & endpoints
Mirror the demo's `backend/api/envelope.py`: every response `{status, data, message}`, `HTTPException`
→ error envelope, pydantic 422 → error envelope. Accept `Content-Encoding: gzip`. Reject bodies over
`MC_MAX_BODY_MB`.

**DoD:**
- [ ] `/health` → `{status:"ok", data:{version, contract_version:"1"}}`
- [ ] `POST /v1/jobs` 202; 422 on invalid; 413 on too large
- [ ] `GET /v1/jobs/{id}` status + per-test progress; 404 unknown/expired
- [ ] `GET /v1/jobs/{id}/result` 409 until done, 200 with `SimulationResponse` after
- [ ] `DELETE /v1/jobs/{id}` sets cancel flag; job ends `cancelled`

### Task 1.3 – Contract
Models from the parent tracker. Validators: at least one test section; all per-record arrays in a
section have equal length; `node_members` indices in range; `alpha ∈ (0, 0.5]`; `n_sims ≥ 19`
(so p can reach 0.05).

### Task 1.4 – Jobs
Job = `{id, status, dataset_id, created, progress{test:{done,total}}, result, error, cancel: Event}`.
Tests inside a job run sequentially; a failure in one test marks that test `failed` with the message and
continues the others (partial results are useful). Inputs are dropped when the job finishes.

### Task 1.5 – Engine
- `simulate(draw_null, cfg)`: seeds `SeedSequence(cfg.seed).spawn(cfg.n_sims)`; runs with joblib
  (`loky`) when `n_jobs > 1`, in-process otherwise; checks cancel flag + `max_seconds` between chunks;
  calls `progress(done, total)`.
- `p_value(obs, null, tail)` with `+1` correction; `tail ∈ {greater, less, two-sided}` (two-sided =
  `min(1, 2·min(p_greater, p_less))`).
- `bh_qvalues(p)` monotone, clipped to 1; NaN-safe (NaN stays NaN).
- `nulls.py`: `gaussian_surrogate(X, rng)` (same mean + covariance, `rng.multivariate_normal`, handles
  singular covariance via `method="svd"`), `column_shuffle(X, rng)`, `permute(a, rng)`,
  `circular_shift_within_blocks(a, blocks, order, rng)`.

**Gotchas:**
- Chunked parallel runs must not change which seed a simulation gets — index-based seeds only.
- Results must be JSON-safe: convert numpy scalars, NaN → `None`.

## Git Commit
`[impl] Monte Carlo service: skeleton, contract, job API and simulation engine`
