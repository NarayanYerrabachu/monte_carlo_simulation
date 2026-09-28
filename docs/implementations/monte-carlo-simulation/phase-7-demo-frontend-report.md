# Phase 7: Demo Frontend & Report

**Status**: ⏸️ Not Started
**Parent Tracker**: `docs/implementations/monte-carlo-simulation.md`
**Repo**: CorteXplorer_tda_demo

## Goal
Make Monte Carlo results visible wherever the finding is shown, and in the report.

## End-of-Phase System State
- A **Run significance tests** control with per-test progress.
- Badges / greyed-out states on loops, pairs, pre-event bands, anomalies, Mapper nodes.
- Report "Statistical validation" section.

## Tasks

| Task | Files | Complexity | Status | Description |
|------|-------|------------|--------|-------------|
| 7.1 | `frontend/js/monte_carlo.js` | Medium | ⏸️ | Run button, polling (`apiJson`/`postJson`), progress, shared result store |
| 7.2 | persistence diagram JS | Medium | ⏸️ | MC noise band line next to heuristic band; `n_significant`; null histogram |
| 7.3 | `frontend/js/relationships_ext.js` + topological view | Medium | ⏸️ | p/q per pair; non-significant pairs grey (same style as frequency-only) |
| 7.4 | `frontend/js/relationships_ext.js` (pre-event) | Low | ⏸️ | p per band × metric, significance marker |
| 7.5 | `frontend/js/tda_mapper.js`, anomaly lists | Medium | ⏸️ | `stable 92 %` / CI in tooltips, detail panel, risk groups |
| 7.6 | `backend/services/report_data.py`, `report_view.js`, `pdf_builder.py`, Excel export | Medium | ⏸️ | Validation section; method text (null model, n, α) |

**DoD:**
- [ ] No value invented in JS: not computed ⇒ "–"; errors visible (no silent empty state)
- [ ] All dynamic HTML built in JS; Python returns JSON only
- [ ] Every surface listed above shows the new fields (3D graph tooltip, detail panel, risk groups, PDF, Excel)

## Git Commit
`[impl] Monte Carlo: significance and stability in UI and report`
