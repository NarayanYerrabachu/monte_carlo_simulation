# Monte Carlo Simulation Service for CortXplorer TDA - Implementation Plan

**Status**: 🔄 In Progress

**Last Updated**: 2026-09-28

## Requirements & Context

### Core Requirements
- **What:** A standalone **Monte Carlo HTTP service** (this repo, FastAPI) that receives the TDA and
  statistical data computed by the CortXplorer TDA demo (`../CorteXplorer_tda_demo`) in a request, runs
  Monte Carlo simulations on it and returns **significance** (p-values, q-values, empirical noise bands)
  and **stability** (confidence intervals, reproduction rates) in the response. Plus the demo-side
  client, endpoints, UI and report integration.
- **Why:** Every TDA finding in the demo is descriptive only. `compute_persistent_homology` states it
  literally: *"Descriptive only — nothing here is significance-tested."* A user cannot tell a real loop,
  relationship, pre-event signal or anomaly from one that random data would produce as well.
- **Constraint:**
  - **Request / response over HTTP.** The demo owns data loading and TDA; the service owns simulation. No
    Python imports between the repos — the JSON contract is the only coupling.
  - Deterministic: same payload + same seed ⇒ identical response, independent of worker count.
  - Local only (runs next to the demo in docker compose; no external services).
  - Long simulations are **asynchronous jobs** (submit → poll → result); the demo never blocks a request
    thread for minutes.
  - The demo keeps working without the service (`MC_SERVICE_URL` unset or service down ⇒ Monte Carlo
    features show "not available", like the optional LLM).
- **Success metric:**
  - On synthetic data with planted structure (circle, label association, pre-event shift, stable outlier)
    the corresponding test is significant / stable; on pure noise it is not (false-positive rate ≈ α).
  - Every loop, topological pair, pre-event band, HIGH anomaly and Mapper node in the demo UI shows a
    significance or stability value, or "–" while not computed.

### Feature Specifications

Four Monte Carlo tests behind one simulation engine:

| Test | Question | Null / resampling | Output per finding |
|---|---|---|---|
| **Loop (H1) significance** | Is this loop more persistent than loops in structureless data of the same shape? | Surrogates: `gaussian` (same mean + covariance, default) or `shuffle` (columns permuted independently) | Empirical noise band (95th pct of null **max** H1 persistence), p per loop, `significant`, `n_significant` |
| **Relationship permutation** | Does label A sit in label B's region of the shape more than chance? | Mapper membership fixed; B labels permuted (circular shift within ticker for time series, plain permutation otherwise) | p, BH q, `significant` per pair |
| **Pre-event pseudo-events** | Do the 6–4 / 4–2 / 2–0 week bands before an event differ from random windows? | Same number of pseudo-events per ticker at random days with enough history | p per band × metric (feature z, anomaly mean, share ≥ 0.6, event-region share) |
| **Anomaly & Mapper stability** | Does a HIGH anomaly / Mapper node survive resampling, reseeding and small parameter changes? | Bootstrap rows + reseed IsolationForest; perturb Mapper `n_intervals` / `overlap` / `eps` | Anomaly: mean, 95 % CI, `p_high` (share of runs ≥ 0.60), top-K Jaccard. Mapper: per-node stability (best-match Jaccard), cluster ARI |

**User interaction (demo):**
1. User loads a dataset (existing flow). Nothing changes until Monte Carlo is requested.
2. User clicks **Run significance tests** (TDA Explorer / Relationships).
3. Demo builds the payload from `_state`, submits it to the service, shows per-test progress.
4. Findings get badges (`p = 0.004`, `stable 92 %`); non-significant pairs are greyed like the existing
   frequency-only pairs; the persistence diagram shows the Monte Carlo noise band next to the heuristic.
5. The PDF / HTML report gets a "Statistical validation" section.

### Current System State
Demo (`../CorteXplorer_tda_demo`, FastAPI on port 8010, docker compose service `cortexplorer-demo`):
- `backend/tda/engine.py::compute_persistent_homology` — PCA(≤5) → sample `TDA_SAMPLE_N=3000` → ripser
  maxdim 1; noise band = `max(2 × median H1 persistence, 1e-3)`; `loop_class`, `persistence_ratio`
  descriptive only.
- `backend/services/topo_relationships.py` — footprint (≥ 2 records and share ≥ `REGION_LIFT`× base),
  weighted-Jaccard overlap, lift = √(e_ab · e_ba); fixed cut-off `TOPO_LIFT_NOTABLE = 1.5`, no test.
- `backend/services/pre_event.py::pre_event_patterns` — finds events itself, compares band stats with a
  whole-dataset baseline; no null distribution.
- `backend/tda/engine.py::detect_anomalies` — 0.6 × IsolationForest + 0.4 × distance to cluster centroid,
  min-max normalised; one seed (`TDA_SEED=42`); ≥ 0.60 HIGH.
- `backend/tda/mapper.py::run_mapper` — interval cover on the lens + `_dbscan_capped` per interval.
- `httpx` is already a demo dependency. Per-dataset caches are evicted in `_rebuild_state_from_df`.
- API convention in the demo: every `/api/*` response is `{status, data, message}`.

This repo is empty (created 2026-09-28).

### Out of Scope
- Changing the demo's algorithms (lenses, Mapper, anomaly formula). Monte Carlo only *measures* them.
- Auth between demo and service (both on the internal compose network). Add before any non-local deployment.
- Persisting jobs across service restarts (in-memory job store with TTL).
- Forecasting-style Monte Carlo (simulating future project costs). Possible follow-up.
- Horizontal scaling / job queue (Celery, Redis). One process with a worker pool is enough for the demo.

### Related Decisions
- **Separate HTTP service** (user decision 2026-09-28): the demo forwards TDA + statistical data, the
  service answers with simulation results — request/response.
- **Async job API instead of one blocking call** — loop and stability tests take minutes on 25k rows;
  HTTP timeouts and the demo's request threads must not depend on that. Cheap tests still go through the
  same job API (one code path).
- **Algorithms the null model must re-run live in the service** (ripser, lift, band statistics,
  IsolationForest score, Mapper). Over HTTP the service cannot call back into the demo, so these are
  re-implemented in the service with the demo's parameters sent in the request, and guarded by
  **parity tests** (fixed payload → service result == demo result). The request carries every parameter
  (weights 0.6/0.4, contamination, `REGION_LIFT`, thresholds, Mapper params, seed) so the service has no
  hidden defaults that can drift.
- **Family-wise noise band for loops** — each observed loop is compared with the null distribution of the
  **maximum** persistence per surrogate, which controls false loops across the whole diagram.
- **Default loop null = `gaussian`** — stricter than `shuffle` (a correlated ellipsoid blob must not count
  as a loop).
- **The demo sends the data it already computed** (normalised feature matrix, labels, Mapper membership,
  timeline, anomaly scores, observed statistics). The service never loads files or re-derives features.

## Overview

### What We Want to Do
Build the `monte-carlo-service` (FastAPI, port **8020**) with a versioned JSON contract, one simulation
engine and four tests; add a client in the demo that builds the payload, submits jobs, polls and serves
the results to the UI.

### Why We Need This
- **Problem:** Findings carry no uncertainty; thresholds (2 × median, lift 1.5, score 0.60) are fixed
  heuristics independent of sample size.
- **Gap:** No null model, no resampling, no multiple-testing control.
- **Impact:** For public-sector users a false "relationship" or "loop" is a wrong statement.
- **Solution:** Empirical p-values from simulated nulls, BH correction, bootstrap stability — computed in a
  separate, independently scalable service.

### Approach
1. Service skeleton: contract models, job store, worker pool, simulation engine.
2. Four test modules on top of the engine, each a pure function `(request model, SimConfig) → result model`.
3. Demo: `services/monte_carlo_client.py` (payload builder + httpx client) and `api/monte_carlo.py`
   (proxy endpoints in the demo envelope); frontend renders the results.

### Architectural Decision

**Decision:** Separate FastAPI microservice with an async job API and a versioned pydantic contract.

**Why this approach:**
- Clear ownership: demo = data + TDA, service = statistics. Matches evocenta's microservice setup (ECS).
- Heavy CPU work runs in a separate container with its own CPU/memory limits; the demo stays responsive.
- Testable in isolation with synthetic payloads; reusable by other CortXplorer editions.

**Alternatives considered:**
- *Library imported by the demo* — simpler, but not the requested request/response architecture and
  shares the demo's process/memory.
- *Synchronous POST returning results* — breaks on multi-minute runs (timeouts, blocked workers).
- *Service calls back into the demo for statistics* — circular dependency between services; rejected.

**Pattern followed:** demo `api/relationships.py` (router + `EnvelopeRoute`), demo `services/llm.py`
(optional external dependency that degrades gracefully).

## Shared Definitions

### HTTP API (service, port 8020)

| Method | Path | Body / result |
|---|---|---|
| `GET` | `/health` | `{status: "ok", version, contract_version}` |
| `POST` | `/v1/jobs` | `SimulationRequest` → `202 {job_id, status: "queued"}` |
| `GET` | `/v1/jobs/{job_id}` | `{job_id, status: queued\|running\|done\|failed\|cancelled, progress: {test: {done, total}}, error?}` |
| `GET` | `/v1/jobs/{job_id}/result` | `SimulationResponse` (409 until done) |
| `DELETE` | `/v1/jobs/{job_id}` | cancel (used when the demo swaps datasets) |

Responses use the same `{status, data, message}` envelope as the demo so the demo client can reuse its
handling. Payloads are gzip-encoded JSON (`Content-Encoding: gzip`); 25k × 10 floats ≈ 2 MB raw.
Max body size configurable (`MC_MAX_BODY_MB`, default 100).

### Contract (pydantic, `contract_version = "1"`)

```python
class SimSettings(BaseModel):
    n_sims: int = 200; n_sims_perm: int = 999; seed: int = 42; alpha: float = 0.05
    max_seconds: float | None = 600

class SimulationRequest(BaseModel):
    contract_version: Literal["1"]
    dataset_id: str                    # demo generation id; echoed back, used to discard stale results
    settings: SimSettings
    loops: LoopsInput | None = None
    relationships: RelationshipsInput | None = None
    pre_event: PreEventInput | None = None
    anomaly_stability: AnomalyInput | None = None
    mapper_stability: MapperInput | None = None     # at least one test required

class LoopsInput(BaseModel):          # demo already did PCA; service samples + runs ripser
    X: list[list[float]]               # PCA-projected points (≤ 5 dims)
    sample_n: int = 1000; null: Literal["gaussian", "shuffle"] = "gaussian"
    observed_noise_threshold: float | None   # the demo's heuristic, for side-by-side display

class RelationshipsInput(BaseModel):
    labels_a: list[str]; labels_b: list[str]           # index-aligned with records
    node_members: list[list[int]]                       # Mapper membership (record positions)
    region_lift: float = 2.0; min_label_records: int = 2
    blocks: list[str] | None = None                     # ticker per record → circular shift within block
    order: list[int] | None = None                      # time order within block

class PreEventInput(BaseModel):
    ticker: list[str]; pos_in_ticker: list[int]         # timeline
    event_rows: list[int]                               # positions of real events
    Z: list[list[float]]; feature_names: list[str]      # standardised features
    anomaly: list[float | None]; high_threshold: float = 0.6
    event_region: list[bool] | None                     # record in Mapper event region
    bands: list[tuple[int, int]]                        # weeks, e.g. [(6,4),(4,2),(2,0)]
    days_per_week: int = 5

class AnomalyInput(BaseModel):
    X: list[list[float]]; record_ids: list[str]; cluster_labels: list[int]
    contamination: float; iso_weight: float = 0.6; topo_weight: float = 0.4
    high_threshold: float = 0.6; top_k: int = 30

class MapperInput(BaseModel):
    X: list[list[float]]; lens: list[float]
    n_intervals: int; overlap: float; eps: float; min_samples: int
    perturb: dict = {"n_intervals": [-2, 2], "overlap": [-0.1, 0.1], "eps": [0.9, 1.1]}
    eval_sample_n: int = 2000
    node_members: list[list[int]]; node_ids: list[int]  # observed graph to score

class SimulationResponse(BaseModel):
    contract_version: str; dataset_id: str; job_id: str
    loops: TestResult | None; relationships: TestResult | None; pre_event: TestResult | None
    anomaly_stability: TestResult | None; mapper_stability: TestResult | None
    errors: dict[str, str]            # per failed / not-implemented test: why (others still returned)

class TestResult(BaseModel):
    test: str; n_completed: int; stopped_early: bool; elapsed_s: float
    config: dict; results: list[dict]; summary: dict
```
Unavailable values are `null` (demo renders "–"); never fabricated defaults. Per-finding join keys:
loops → index in the observed H1 list, pairs → `(a, b)`, bands → `(band, metric)`, anomalies →
`record_id`, Mapper → `node_id`.

### Engine (service-internal)
```python
def simulate(draw_null: Callable[[np.random.Generator], Any], cfg: SimConfig) -> SimResult
    # sim i uses default_rng(SeedSequence(cfg.seed).spawn(n)[i]) → reproducible for any worker count
def p_value(observed, null, tail="greater") -> float     # (1 + #{null ≥ obs}) / (1 + n)
def bh_qvalues(p) -> np.ndarray
def percentile_ci(samples, level=0.95) -> tuple[float, float]
```

### Configuration & Flags

Service:
| Variable | Default | Meaning |
|---|---|---|
| `MC_PORT` | 8020 | Service port |
| `MC_WORKERS` | 2 | Parallel jobs |
| `MC_N_JOBS` | 1 | Processes per job for simulations |
| `MC_MAX_BODY_MB` | 100 | Request size limit |
| `MC_JOB_TTL_S` | 3600 | Finished jobs kept in memory |

Demo:
| Variable | Default | Meaning |
|---|---|---|
| `MC_SERVICE_URL` | unset | e.g. `http://monte-carlo:8020`; unset ⇒ feature disabled |
| `MC_N_SIMS` / `MC_N_SIMS_PERM` | 200 / 999 | Sent in `SimSettings` |
| `MC_ALPHA` | 0.05 | Sent in `SimSettings` |
| `MC_MAX_SECONDS` | 600 | Time budget per test |
| `MC_LOOP_NULL` | gaussian | `gaussian` \| `shuffle` |
| `MC_HTTP_TIMEOUT_S` | 30 | Per HTTP call (submit/poll), not the job |

### Conventions
- Both repos: Python 3.13, pipenv (`Pipfile` + `Pipfile.lock`), pytest, `[impl]/[fix]/[test]/[docs]`
  commits, work directly on `main`.
- Service follows the demo's style: routers in `api/`, logic in `services/`, envelope responses.
- Seeds: always `SeedSequence(seed).spawn(...)`; never global `np.random`.
- p-values include the +1 correction (never 0); `n_completed` is always reported.

## Phase Summary

| Phase | Repo | Scope | Status |
|---|---|---|---|
| [1 – Service skeleton, contract & engine](monte-carlo-simulation/phase-1-service-engine.md) | service | FastAPI app, envelope, job store + workers, pydantic contract, `simulate`/p/BH/CI, Dockerfile | ✅ |
| [2 – Loop significance](monte-carlo-simulation/phase-2-loop-significance.md) | service | Surrogate nulls, ripser, noise band, per-loop p | ⏸️ |
| [3 – Relationship permutation](monte-carlo-simulation/phase-3-relationship-permutation.md) | service | Vectorised lift, (block) permutation, BH | ⏸️ |
| [4 – Pre-event pseudo-events](monte-carlo-simulation/phase-4-pre-event.md) | service | Pseudo-event sampler, band null distributions | ⏸️ |
| [5 – Stability](monte-carlo-simulation/phase-5-stability.md) | service | Anomaly bootstrap, Mapper perturbation | ⏸️ |
| [6 – Demo client & endpoints](monte-carlo-simulation/phase-6-demo-client.md) | demo | Payload builder, httpx client, proxy endpoints, eviction/cancel, compose | ⏸️ |
| [7 – Demo frontend & report](monte-carlo-simulation/phase-7-demo-frontend-report.md) | demo | Badges, noise band, histograms, report section | ⏸️ |
| [8 – Tests & quality gate](monte-carlo-simulation/phase-8-tests.md) | both | Calibration, parity, contract, API, end-to-end in compose | ⏸️ |

Phases 2–5 depend only on phase 1 and can run in any order. Phase 6 needs the contract from phase 1 (it can
start against stubbed results); 7 needs 6.

## Architecture and Design

### System Changes

```
monte_carlo_simulation/                         (this repo — service)
  Pipfile, Pipfile.lock, .python-version, Dockerfile, docker-compose.yml (standalone dev)
  mc_service/
    main.py              FastAPI app, EnvelopeRoute, /health
    api/jobs.py          /v1/jobs endpoints
    contract.py          pydantic request/response models (contract_version "1")
    jobs.py              in-memory job store, worker pool, cancel, TTL, progress
    engine.py            SimConfig, SimResult, simulate, p_value, bh_qvalues, percentile_ci
    nulls.py             gaussian_surrogate, column_shuffle, permute, circular_shift_within_blocks
    simulations/loops.py            loop_significance
    simulations/relationships.py    footprint_lift (vectorised), relationship_significance
    simulations/pre_event.py        sample_pseudo_events, pre_event_significance
    simulations/stability.py        anomaly_stability, mapper_stability
  tests/
    fixtures/            synthetic payloads + demo-parity payloads (JSON)

CorteXplorer_tda_demo/                          (phase 6–7)
  backend/services/monte_carlo_client.py   build_request(state, tests) + submit/poll/result via httpx
  backend/api/monte_carlo.py               POST /api/monte-carlo/run, GET /status, GET /results
  backend/main.py                          dataset_id per rebuild; evict + cancel job in _rebuild_state_from_df
  docker-compose.yml                       + monte-carlo service, MC_SERVICE_URL for the demo
  frontend/js/monte_carlo.js + badges in the existing screens
  backend/services/report_data.py          "validation" block
```

### Data Flow
```
Browser ──POST /api/monte-carlo/run {tests}──▶ Demo (8010)
  Demo: require_pipeline(); snapshot under _lock → build SimulationRequest (gzip JSON)
  Demo ──POST /v1/jobs──▶ MC service (8020) ──202 {job_id}
  Demo stores {job_id, dataset_id} in _state["_monte_carlo"]
Browser ──GET /api/monte-carlo/status (poll)──▶ Demo ──GET /v1/jobs/{id}──▶ Service
Browser ──GET /api/monte-carlo/results──▶ Demo ──GET /v1/jobs/{id}/result──▶ Service
  Demo checks dataset_id matches current dataset; caches the response; joins to findings
Dataset rebuild ─▶ Demo evicts cache + DELETE /v1/jobs/{id}
```

### Key Integration Points
- **Snapshot under `_lock`** when building the payload; after that the demo does not hold the lock.
- **Stale-result guard:** `dataset_id` echoed back; mismatch ⇒ result dropped.
- **Graceful degradation:** `MC_SERVICE_URL` unset ⇒ `/api/monte-carlo/*` return 503 "Monte Carlo
  service not configured"; service down ⇒ 502 with a visible message (never empty/zero results).
- **Parity:** re-implemented statistics validated against demo functions on fixed payloads exported from
  the demo's `_make_mock_df` (fixtures committed in this repo, regenerated by a demo script).

## Configuration

### New Environment Variables
See *Configuration & Flags*. Demo `.env.example`, README, `docs/architecture.md`; service README.

### Configuration Changes
Demo `docker-compose.yml`: add `monte-carlo` service (build from `../monte_carlo_simulation` or image
`yerran/monte-carlo-service`), `MC_SERVICE_URL=http://monte-carlo:8020` for `cortexplorer-demo`.

## Final Quality Gate ⏸️

**Focus:** both repos green; results deterministic; demo unchanged when the service is absent.

**Definition of Done:**
- [ ] Service: `pipenv run pytest -q` 100 % pass, incl. calibration and parity tests
- [ ] Demo: full suite passes (baseline 110 passed / 6 skipped + new tests)
- [ ] Same request + seed ⇒ byte-identical response; `MC_N_JOBS=1` vs `4` identical
- [ ] `docker compose up` runs both; end-to-end run on gov-aid (25k) finishes all tests within
      `MC_MAX_SECONDS` at defaults on a dev laptop
- [ ] Demo without `MC_SERVICE_URL` behaves exactly as before

**Git Commit:** `[test] Quality gate: Monte Carlo service integration and regression verification`

## Documentation ⏸️

**Handled by:** `doc-writer` agent.
**Areas:** service README (API, contract, statistics, nulls, env), demo README + `docs/architecture.md`
(new endpoints, env vars, data flow, compose), demo `.env.example`, demo project skill.

**Git Commit:** `[docs] Documentation for Monte Carlo simulation service`

## Testing Strategy

### Unit Testing (service)
- Engine: determinism across worker counts, p never 0, BH against a hand-computed example, time budget
  stops early and reports it.
- Planted signal: noisy circle ⇒ ≥ 1 significant loop; Gaussian blob ⇒ none. Planted label association
  ⇒ q < α; independent labels ⇒ none. Shifted pre-event window ⇒ p < α. Far outlier ⇒ `p_high` ≈ 1.
- **Calibration:** under a true null, p ~ Uniform(0,1) (KS test, fixed seed); false-positive rate ≤ α + tol.
- Contract: invalid payloads (length mismatch, no test selected, wrong version) ⇒ 422 envelope.

### Integration Testing
- Service API with `TestClient`: submit → poll → result, cancel, 409 before done, 404 unknown/expired job.
- Parity: lift, band statistics, anomaly score, Mapper membership == demo output on committed fixtures.
- Demo: client mocked with `httpx.MockTransport`; 503 without URL, 502 on service error, stale
  `dataset_id` dropped, eviction + cancel on rebuild.
- End-to-end: docker compose, both containers, one run per test on the mock dataset.

### Manual Testing
- Load gov-aid and a financial dataset; run all tests; check badges, greyed pairs, noise band, report.

## Risks

| Risk | Mitigation |
|---|---|
| Re-implemented algorithms drift from the demo | Every parameter sent in the request; parity tests with committed fixtures; fixture regeneration script in the demo |
| Payload size (25k × features, Mapper membership) | gzip; send only the sections of selected tests; body limit; loops send the PCA sample only |
| ripser × 200 too slow | `sample_n=1000` for observed and null alike; time budget with `stopped_early` |
| Time-series autocorrelation inflates significance | Circular shift within ticker for dated data |
| Mapper stability O(n²) co-membership | Evaluate on a fixed record sample (≤ 2000) |
| Min p = 1/(n+1) too coarse for BH over many pairs | Relationships default 999 permutations |
| Service holds data in memory | Job TTL; results only, inputs dropped after the job finishes; internal network only |
