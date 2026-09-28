# Phase 3: Relationship Permutation Test

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: monte_carlo_simulation

## Goal
A p-value and BH q-value for every topological (A, B) pair from the demo's
`GET /api/relationships/topological`.

## End-of-Phase System State
- A job with a `relationships` section returns observed lift, p, q and `significant` per pair.
- Vectorised lift equals the demo's `topo_relationships.topological_relationships` on committed fixtures.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 3.1 | `mc_service/simulations/relationships.py` | High | ⏸️ | `footprint_lift(a_codes, b_codes, M)` — all pair lifts as a matrix |
| 3.2 | `mc_service/simulations/relationships.py` | Medium | ⏸️ | Permutation null (plain or circular shift within blocks) + p/q |
| 3.3 | `tests/test_relationships.py`, `tests/fixtures/topo_*.json` | Medium | ⏸️ | Planted association, independence, parity fixture |

## Detailed Task Descriptions

### Task 3.1 – Vectorised lift
Same definitions as the demo (`backend/services/topo_relationships.py`):
- `M` = sparse node × record membership (`scipy.sparse.csr_matrix`), `sizes = M.sum(1)`.
- Label one-hot `A` (records × a-values); `counts = M @ A`; `share = counts / sizes`;
  `region = (counts ≥ min_label_records) & (share ≥ region_lift × base)`.
- Record-in-footprint mask per label: `(M.T @ region) > 0`.
- `e_ab = mean(inB_footprint[a records]) / mean(inB_footprint)`, same for `e_ba`;
  `lift = sqrt(e_ab · e_ba)`; undefined (None) when either is 0/undefined — as in the demo.
- Only pairs with shared footprint weight > 0 are reported (demo rule).

### Task 3.2 – Null
- Statistic per pair = lift; permute `labels_b` (Mapper membership and `labels_a` fixed).
- If `blocks` given (ticker): circular shift of B within each block along `order` — keeps regime runs
  intact, so autocorrelation does not fake significance. Else plain permutation.
- Pairs whose lift is undefined in a permutation count as "not ≥ observed".
- `p` one-sided greater (lift above expectation), plus `p_below` for lift < 1 reporting;
  `q = bh_qvalues(p)` over all reported pairs; `significant = q < alpha`.
- `n_sims_perm` default 999.

**Result fields:** `results = [{a, b, lift, p_value, p_below, q_value, significant, null_lift_p95}]`,
`summary = {n_pairs, n_significant, permutation: "plain"|"circular_block"}`.

**DoD:**
- [ ] Parity: lifts match the demo within 1e-4 on the fixture (fixture generated in phase 6 by a demo script; stub fixture first)
- [ ] Independent labels ⇒ ≤ α fraction significant; planted association ⇒ q < α
- [ ] 999 permutations on 25k records / ~200 nodes / 10×10 labels < 60 s single core

**Gotchas:**
- Labels "N/A" / "nan" are excluded exactly as in the demo `_footprints`.
- Permuting B changes B's **footprints** too — recompute region each time (that is the point).

## Git Commit
`[impl] Monte Carlo service: topological relationship permutation test`
