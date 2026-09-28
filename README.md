# Monte Carlo Simulation Service (CortXplorer TDA)

A FastAPI service (port **8020**) that attaches **significance** and **stability** to the findings of the
[CortXplorer TDA demo](../CorteXplorer_tda_demo). The demo sends its TDA and statistical data in an HTTP
request. The service runs Monte Carlo simulations as an asynchronous job and returns the results.

```
Demo (8010) ── POST /v1/jobs {TDA + statistical data} ──▶ Monte Carlo service (8020)
            ◀── 202 {job_id} ─────────────────────────────
            ── GET /v1/jobs/{id} (progress) ─────────────▶
            ◀── GET /v1/jobs/{id}/result {p-values, CIs} ─
```

Tests:

- **Loop (H1) significance.** Compares persistence against surrogate data and gives an empirical noise band.
- **Fleet operations (`fleet`).** Takes real fleet records (one per vehicle and day) with their TDA
  regime and ML anomaly score, as sent by CortXplorer. Each simulated day draws a real historical day
  and resamples its vehicle records. It returns the probability of meeting the on-time SLA, the
  delivery-time distribution against a target, vehicles required (with the 95th percentile), fleet
  availability, fuel, breakdown risk and maintenance cost, broken down per TDA regime. Excluding ML
  anomalies is optional, and breakdown or absent-driver records are never excluded.
- **Planned (phases 3–5):** relationship permutation, pre-event pseudo-events, and anomaly and Mapper
  stability.

## Run

**Docker** (image `yerran/monte-carlo-service`, container `monte-carlo-service`):

```bash
docker compose up -d --build        # → http://localhost:8020/health, API docs at /docs
docker compose logs -f monte-carlo
docker compose down
```

**Local** (pipenv virtualenv named `montecarlo`, Python 3.13):

```bash
export PIPENV_CUSTOM_VENV_NAME=montecarlo
pipenv install --dev
pipenv run uvicorn mc_service.main:app --host 0.0.0.0 --port 8020 --reload
pipenv run pytest -q
```

Layout: `mc_service/` is Python only (API, simulations, PDF/Excel builders). All HTML, JS and CSS
live in `frontend/` (`index.html` = live viewer, `fleet_report.html` = report; assets in `frontend/js`
and `frontend/css`). The service serves them from there (`MC_FRONTEND_DIR` overrides the path).

Dependencies live in `Pipfile` / `Pipfile.lock` only. The Docker build installs with `--deploy`, so run
`pipenv lock` after editing the Pipfile.

## API

**Requests and responses are JSON.** Requests that carry data send a JSON body; the GET endpoints
only take the job id in the path (plus `since` on the live feed). Every response is the
`{status, data, message}` envelope, the same as the demo. A generated file is returned inside
`data` like this:

```json
{"filename": "fleet-monte-carlo-10000-seed42.pdf", "content_type": "application/pdf",
 "encoding": "base64", "size_bytes": 465720, "content": "JVBERi0xLjQK…"}
```

| Method | Path | |
|---|---|---|
| `GET` | `/health` | Version and contract version |
| `POST` | `/v1/jobs` | Submit a `SimulationRequest` (JSON, optionally `Content-Encoding: gzip`). Returns `202 {job_id}` |
| `GET` | `/v1/jobs/{id}` | Status (`queued`, `running`, `done`, `failed` or `cancelled`) and per-test progress |
| `GET` | `/v1/jobs/{id}/result` | `SimulationResponse`. Returns 409 while the job is still running or after it was cancelled |
| `DELETE` | `/v1/jobs/{id}` | Cancel the job |
| `POST` | `/v1/jobs/{id}/rerun` | Re-run a fleet job on the same records with other parameters: `{"n_sims": 10000, "fleet": {"fleet_size": 100, "sla_on_time": 0.9}}` |
| `GET` | `/v1/jobs` | All jobs, newest first |
| `GET` | `/v1/jobs/{id}/live?since=N` | Live feed: new null values, the latest surrogate, and the running p-value and noise band |
| `GET` | `/` | **Live viewer**: 3D view of the data vs. the random surrogate, plus the null distribution as it builds up |
| `GET` | `/?job=<id>` | **Live viewer** (fleet): CortXplorer's Monte Carlo ↗ lands here, and the simulated days play out as a live dashboard |
| `GET` | `/report?job=<id>` | **Job report**: HTML view of a finished job, with Download PDF / Excel and what-if re-runs (`/fleet?job=` is the same page) |
| `POST` | `/v1/reports/job` | `{"job_id": "…", "format": "json" \| "pdf" \| "xlsx"}`: the report of a finished job; files come back as base64 in the JSON |
| `GET` | `/report` | Assumption-based fleet report (no job), with PDF and Excel download buttons |
| `POST` | `/v1/reports/fleet` | Body `{"n": 10000, "seed": 42}`. Returns the fleet-management results (numbers and texts) |
| `POST` | `/v1/reports/fleet/pdf` | Same body. Returns the report as a PDF, inside the JSON (base64) |
| `POST` | `/v1/reports/fleet/xlsx` | Same body. Returns the report as Excel, inside the JSON (base64): summary, inputs, distributions, drivers, fleet sizing, convergence, all scenarios |

The request format is defined in [mc_service/contract.py](mc_service/contract.py) (`contract_version` "1").
Configuration is in [.env.example](.env.example).

## Live viewer

Open http://localhost:8020/ and click **Run simulation**. The page submits seeded synthetic data (a
noisy circle, a correlated blob or two rings) and then shows live:

- a rotating 3D view of the observed points (blue) next to the surrogate that the current simulation
  ran on (orange)
- the null distribution filling up, with lines for the observed value, the 95% noise band and the
  demo's heuristic
- the running p-value, then the loop table with a verdict for each loop once the job finishes

Jobs the demo submits show up in the job picker too. `/?job=<id>` opens a specific job; the
demo's **Open live view** link uses it.

## Flow: CortXplorer → live viewer → report

1. In CortXplorer, load a fleet table and press **Monte Carlo ↗**. CortXplorer sends the records,
   each record's TDA regime and ML anomaly score, plus the TDA/ML findings.
2. The job lands in the **live viewer** (`/?job=<id>`). The sample dashboard fills up day by day:
   - KPI tiles: fleet availability, on-time delivery probability, daily fuel cost, breakdown risk and
     P(meet SLA)
   - charts: vehicle requirement, delivery outcome, maintenance cost and on-time share
   - **2D / 3D switch** on every chart. In 3D the distributions become waterfalls, showing the
     distribution after every 10% of the days so you see it settle, and the outcome chart becomes a
     joint *vehicles required × on-time share* surface
   - the **TDA Mapper graph in 3D**, laid out like CortXplorer's TDA Mapper (topological spread on
     x and y, filter height on z). Node size is the number of records and colour is the on-time
     share; the groups holding the simulated day's vehicles light up during playback

   100,000 days compute in a few seconds, so the viewer plays the simulated days back over 15, 30
   or 60 seconds (the **Animation** setting), with **Pause** and **Replay**. It shows how many days
   have been revealed, and the tiles and lines always show the service's running KPIs for that point.
3. When it is done, **Generate report ↗** opens `/report?job=<id>`, the full report, with
   **Download PDF** and **Download Excel** (built by `mc_service/job_report.py`).

The job report shows:

- the observed inputs
- the probabilistic outputs (SLA probability, vehicles required with P95, fuel, breakdown risk,
  maintenance cost)
- KPI tiles
- charts: delivery time against the target, vehicle requirement, outcome, maintenance cost
- the TDA regimes, the TDA/ML findings and convergence

**What-if** re-runs the same records with another fleet size, target or SLA (`/rerun`). Demand stays
at the historical level, so a bigger fleet shows its effect on the same work. The fleet input is kept
until the job TTL (`MC_JOB_TTL_S`) so re-runs work without CortXplorer.

## Fleet-management report (assumption-based)

`mc_service/fleet/` simulates one operating day of a delivery fleet 10,000 times. The inputs are
the ranges from the fleet-management brief (vehicles 450–500, breakdowns 1–5%, drivers 90–98%,
demand 8,000–12,000, traffic 5–60 min, fuel 7–10 L/100 km, maintenance 2–10 h) plus labelled
assumptions. The report covers:

- on-time delivery and the vehicles required
- variable cost (fuel, maintenance, overtime)
- risk drivers (Spearman ρ)
- a what-if for fleet size vs. driver pool
- convergence of the estimate, and recommendations

Open **Report** in the live viewer (or go to http://localhost:8020/report). Set the number of
scenarios and the seed, click **Run report**, then **Download PDF** or **Download Excel**. The page
decodes the base64 file from the JSON response into a download. All three
formats come from the same cached run (`build_report(n, seed)`), so their numbers and texts are
identical, and the same seed always gives the same report.

## Status

- Phase 1 is done: the service, request/response format, job API, simulation engine and Docker image.
- Phase 2 is done: the loop (H1) significance test and the live viewer.
- The fleet-management report is done: HTML, PDF and Excel.
- The relationship, pre-event and stability tests arrive in phases 3–5. Until then, requesting one of
  them reports `"not implemented yet"`.
See [docs/implementations/monte-carlo-simulation.md](docs/implementations/monte-carlo-simulation.md).
