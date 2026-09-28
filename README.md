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

The four tests:

- **Loop (H1) significance.** Compares persistence against surrogate data and gives an empirical noise band.
- **Relationship permutation.** Gives a p-value and a Benjamini–Hochberg (BH) q-value per topological label pair.
- **Pre-event pseudo-events.** Gives a p-value per band and metric against random event windows.
- **Anomaly and Mapper stability.** Uses bootstrap sampling, reseeding and parameter perturbation.

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

Dependencies live in `Pipfile` / `Pipfile.lock` only. The Docker build installs with `--deploy`, so run
`pipenv lock` after editing the Pipfile.

## API

Every response uses the envelope `{status, data, message}`, the same as the demo.

| Method | Path | |
|---|---|---|
| `GET` | `/health` | Version and contract version |
| `POST` | `/v1/jobs` | Submit a `SimulationRequest` (JSON, optionally `Content-Encoding: gzip`). Returns `202 {job_id}` |
| `GET` | `/v1/jobs/{id}` | Status (`queued`, `running`, `done`, `failed` or `cancelled`) and per-test progress |
| `GET` | `/v1/jobs/{id}/result` | `SimulationResponse`. Returns 409 while the job is still running or after it was cancelled |
| `DELETE` | `/v1/jobs/{id}` | Cancel the job |
| `GET` | `/v1/jobs` | All jobs, newest first |
| `GET` | `/v1/jobs/{id}/live?since=N` | Live feed: new null values, the latest surrogate, and the running p-value and noise band |
| `GET` | `/` | **Live viewer**: 3D view of the data vs. the random surrogate, plus the null distribution as it builds up |
| `GET` | `/report` | **Fleet report** page, with PDF and Excel download buttons |
| `GET` | `/v1/reports/fleet?n=10000&seed=42` | Fleet-management Monte Carlo results as JSON (numbers and texts) |
| `GET` | `/v1/reports/fleet.pdf?n=…&seed=…` | Same report as a PDF (A4) |
| `GET` | `/v1/reports/fleet.xlsx?n=…&seed=…` | Same report as Excel: summary, inputs, distributions, drivers, fleet sizing, convergence, all scenarios |

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

Jobs the demo submits show up in the job picker too.

## Fleet-management report

`mc_service/fleet/` simulates one operating day of a delivery fleet 10,000 times. The inputs are
the ranges from the fleet-management brief (vehicles 450–500, breakdowns 1–5%, drivers 90–98%,
demand 8,000–12,000, traffic 5–60 min, fuel 7–10 L/100 km, maintenance 2–10 h) plus labelled
assumptions. The report covers:

- on-time delivery and the vehicles required
- variable cost (fuel, maintenance, overtime)
- risk drivers (Spearman ρ)
- a what-if for fleet size vs. driver pool
- convergence of the estimate, and recommendations

Open **Fleet report** in the live viewer (or go to http://localhost:8020/report). Set the number of
scenarios and the seed, click **Run report**, then **Download PDF** or **Download Excel**. All three
formats come from the same cached run (`build_report(n, seed)`), so their numbers and texts are
identical, and the same seed always gives the same report.

## Status

- Phase 1 is done: the service, request/response format, job API, simulation engine and Docker image.
- Phase 2 is done: the loop (H1) significance test and the live viewer.
- The fleet-management report is done: HTML, PDF and Excel.
- The relationship, pre-event and stability tests arrive in phases 3–5. Until then, requesting one of
  them reports `"not implemented yet"`.
See [docs/implementations/monte-carlo-simulation.md](docs/implementations/monte-carlo-simulation.md).
