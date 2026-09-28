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

Status: planning. See [docs/implementations/monte-carlo-simulation.md](docs/implementations/monte-carlo-simulation.md).
