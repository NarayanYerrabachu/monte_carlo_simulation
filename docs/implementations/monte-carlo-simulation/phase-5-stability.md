# Phase 5: Anomaly & Mapper Stability

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: monte_carlo_simulation

## Goal
Show how robust anomaly scores and Mapper nodes are to resampling, reseeding and small parameter changes.

## End-of-Phase System State
- `anomaly_stability`: per record (top-K + all HIGH) mean score, 95 % CI, `p_high`; top-K Jaccard.
- `mapper_stability`: per observed node a stability score; cluster ARI summary.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 5.1 | `mc_service/simulations/stability.py` | Medium | ⏸️ | `anomaly_score(X, cluster_labels, contamination, weights, seed)` — demo formula |
| 5.2 | `mc_service/simulations/stability.py` | Medium | ⏸️ | Bootstrap + reseed replicate loop, per-record aggregation |
| 5.3 | `mc_service/simulations/stability.py` | High | ⏸️ | `mapper(X, lens, params, seed)` — demo algorithm incl. interval rescale + capped DBSCAN |
| 5.4 | `mc_service/simulations/stability.py` | Medium | ⏸️ | Parameter perturbation, best-match Jaccard per node, ARI |
| 5.5 | `tests/test_stability.py` | Medium | ⏸️ | Outlier vs bulk, stable vs fragile node, parity |

## Detailed Task Descriptions

### Task 5.1/5.2 – Anomaly stability
- Score = min-max(`iso_weight · IsolationForest(-score_samples)` + `topo_weight · distance to cluster
  centroid / max`, noise = 1.0) — exactly `engine.detect_anomalies`.
- Replicate r: fit IsolationForest on a bootstrap sample with seed r; score **all** records; cluster
  labels stay the observed ones (clustering stability is Mapper's part).
- Per record: mean, 2.5/97.5 pct, `p_high = share of replicates ≥ high_threshold`.
- `topk_jaccard`: mean Jaccard of each replicate's top-K with the observed top-K.
- Return records = observed top-K ∪ observed HIGH (not all 25k — keep the response small).

### Task 5.3/5.4 – Mapper stability
- Re-implement `backend/tda/mapper.py::run_mapper` membership (cover, per-interval StandardScaler,
  DBSCAN with the pair cap). Given explicit `eps` (the demo sends the eps it actually used, after auto-eps).
- Replicate r: draw params uniformly within `perturb` ranges, bootstrap rows, run Mapper.
- Evaluate on a fixed record sample (`eval_sample_n`): for each observed node, best Jaccard match
  among replicate nodes (restricted to the sample and to records present in the bootstrap);
  node stability = mean over replicates.
- Summary: mean/median node stability, share of nodes ≥ 0.5, ARI of "record → largest node" assignment.

**DoD:**
- [ ] Far outlier ⇒ `p_high ≥ 0.95`; bulk record ⇒ `p_high ≤ 0.05`
- [ ] Two well-separated blobs ⇒ node stability ≥ 0.8; uniform noise ⇒ clearly lower
- [ ] Parity: observed anomaly scores and Mapper membership match the demo on fixtures (seeded)

**Gotchas:**
- The demo's background pipeline Mapper uses `TDA_MAPPER_FIT_CAP=4000`; the demo sends which Mapper
  (explorer vs pipeline) and its parameters — never assume defaults.
- Bootstrap duplicates: deduplicate member indices before Jaccard.

## Git Commit
`[impl] Monte Carlo service: anomaly and Mapper stability`
