"""Are the topological relationships real? Permutation test of the Mapper-footprint lift.

CortXplorer's Topological (TDA) relationship view links a label of group A with
a label of group B when records of each sit where the other concentrates in the
Mapper graph:

  footprint(v)   Mapper nodes where label v is over-represented: ≥ ``min_label_records``
                 of its records and share ≥ ``region_lift`` × its overall share
  enrichment     share of a's records inside b's footprint ÷ share of all records in it
  lift(a, b)     geometric mean of the two enrichments (> 1: they share a region)

A lift is a ratio, not evidence. The test asks: would records with label a sit
inside b's footprint this often if a had nothing to do with b? The footprints
stay as observed; the labels are re-assigned to the records at random — A's
labels for the a-in-b enrichment, B's labels for the b-in-a one — and the lift
is recomputed. With ``blocks`` (time series, one block per ticker) the labels
are shifted circularly within each block instead, which keeps runs of the same
label together (a plain shuffle would call every persistent regime significant).

p-value: two-sided empirical p with the +1 correction; q-value: Benjamini–
Hochberg over all tested pairs. A pair is significant at q ≤ alpha. With many
records even small lifts become significant — the lift stays the effect size.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from mc_service.contract import RelationshipsInput
from mc_service.engine import bh_qvalues, p_values, simulate
from mc_service.simulations.base import RunContext

PLACEHOLDER_LABELS = {"N/A", "nan", "None", "Unknown", "unknown", ""}
MAX_PAIRS = 60                 # pairs reported (all are tested)


def _footprints(labels: np.ndarray, node_members: list[np.ndarray], sizes: np.ndarray, s: RelationshipsInput):
    """Labels with enough support, their index per record (-1 = not tested) and footprint mask over nodes."""
    values, counts_all = np.unique(labels, return_counts=True)
    keep = [str(v) for v, c in zip(values, counts_all, strict=True)
            if str(v) not in PLACEHOLDER_LABELS and c >= s.min_label_support]
    index = {v: j for j, v in enumerate(keep)}
    idx = np.array([index.get(str(v), -1) for v in labels], dtype=int)
    counts = np.zeros((len(node_members), len(keep)), dtype=np.int64)
    for k, members in enumerate(node_members):
        got = idx[members]
        got = got[got >= 0]
        if got.size:
            counts[k] = np.bincount(got, minlength=len(keep))
    totals = np.bincount(idx[idx >= 0], minlength=len(keep))
    base = totals / max(1, len(labels))
    with np.errstate(divide="ignore", invalid="ignore"):
        share = counts / np.maximum(sizes, 1)[:, None]
    region = (counts >= s.min_label_records) & (share >= s.region_lift * base[None, :])
    return keep, idx, region, totals


def _record_masks(region: np.ndarray, node_members: list[np.ndarray], n: int) -> np.ndarray:
    """(n records × labels): the record lies in a footprint node of the label."""
    mask = np.zeros((n, region.shape[1]), dtype=np.float32)
    for j in range(region.shape[1]):
        for k in np.flatnonzero(region[:, j]):
            mask[node_members[k], j] = 1.0
    return mask


def _one_hot(idx: np.ndarray, width: int) -> np.ndarray:
    out = np.zeros((len(idx), width), dtype=np.float32)
    ok = idx >= 0
    out[np.flatnonzero(ok), idx[ok]] = 1.0
    return out


def _enrichment(one_hot: np.ndarray, totals: np.ndarray, foot: np.ndarray, base: np.ndarray) -> np.ndarray:
    """(labels × footprints): share of each label's records inside each footprint ÷ the footprint's base share."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return (one_hot.T @ foot) / totals[:, None] / base[None, :]


def _shuffler(n: int, blocks: list[str] | None, order: list[int] | None):
    """Returns draw(rng) → a permutation of the record positions (labels move, records stay)."""
    if blocks is None:
        return lambda rng: rng.permutation(n)
    groups: dict[str, list[int]] = {}
    for i, b in enumerate(blocks):
        groups.setdefault(b, []).append(i)
    seqs = [np.array(sorted(g, key=(lambda i: order[i]) if order is not None else None)) for g in groups.values()]

    def shift(rng: np.random.Generator) -> np.ndarray:
        perm = np.arange(n)
        for seq in seqs:                                   # the label sequence of a block, rotated
            perm[seq] = np.roll(seq, int(rng.integers(len(seq))))
        return perm
    return shift


def run(section: RelationshipsInput, ctx: RunContext) -> dict:
    s = section
    n = len(s.labels_a)
    a_lab, b_lab = np.asarray(s.labels_a, dtype=str), np.asarray(s.labels_b, dtype=str)
    members = [np.asarray(m, dtype=int) for m in s.node_members]
    sizes = np.array([len(m) for m in members], dtype=np.int64)
    alpha = ctx.settings.alpha

    a_vals, a_idx, a_reg, a_tot = _footprints(a_lab, members, sizes, s)
    b_vals, b_idx, b_reg, b_tot = _footprints(b_lab, members, sizes, s)
    a_foot, b_foot = _record_masks(a_reg, members, n), _record_masks(b_reg, members, n)
    a_hot, b_hot = _one_hot(a_idx, len(a_vals)), _one_hot(b_idx, len(b_vals))
    a_base, b_base = a_foot.mean(axis=0), b_foot.mean(axis=0)
    has_a, has_b = a_reg.any(axis=0), b_reg.any(axis=0)
    testable = has_a[:, None] & has_b[None, :]              # both labels have a footprint

    def lift(a_one_hot: np.ndarray, b_one_hot: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        e_ab = _enrichment(a_one_hot, a_tot, b_foot, b_base)                 # a's records in b's footprint
        e_ba = _enrichment(b_one_hot, b_tot, a_foot, a_base).T               # b's records in a's footprint
        with np.errstate(invalid="ignore"):
            return np.where(testable, np.sqrt(e_ab * e_ba), np.nan), e_ab, e_ba

    observed, e_ab, e_ba = lift(a_hot, b_hot)
    shuffle = _shuffler(n, s.blocks, s.order)

    def draw(rng: np.random.Generator) -> np.ndarray:
        return lift(a_hot[shuffle(rng)], b_hot[shuffle(rng)])[0].ravel()

    ctx.live.set_observed(stat=None, alpha=alpha, mode="none", statistic="lift of the Mapper footprints")
    n_pairs = int(testable.sum())
    if n_pairs == 0:
        return {"test": "relationships", "n_completed": 0, "stopped_early": False, "elapsed_s": 0.0,
                "config": _config(s, ctx, n), "results": [],
                "summary": _summary(s, alpha, 0, 0, a_vals, b_vals, has_a, has_b, a_lab, b_lab,
                                    note="No pair of labels has a Mapper footprint on both sides, so there is nothing to test.")}

    res = simulate(draw, ctx.sim_config(n_sims=ctx.settings.n_sims_perm, chunk_size=max(1, ctx.settings.n_sims_perm // 20)))
    null = res.null.reshape(res.n_completed, *observed.shape)
    p = p_values(observed, null, tail="two-sided")
    q = bh_qvalues(p.ravel()).reshape(p.shape)             # one family: all tested pairs
    with np.errstate(invalid="ignore", all="ignore"):
        null_mean = np.nanmean(null, axis=0)
        null_lo, null_hi = np.nanpercentile(null, [2.5, 97.5], axis=0)
    cooc = (a_hot.T @ b_hot).astype(int)

    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(a_vals):
        for j, b in enumerate(b_vals):
            if not testable[i, j] or not np.isfinite(observed[i, j]):
                continue
            significant = bool(np.isfinite(q[i, j]) and q[i, j] <= alpha)
            pairs.append({
                "a": a, "b": b, "lift": float(observed[i, j]),
                "enrichment_a_in_b": float(e_ab[i, j]), "enrichment_b_in_a": float(e_ba[i, j]),
                "n_cooccur": int(cooc[i, j]),
                "null_mean": float(null_mean[i, j]), "null_lo": float(null_lo[i, j]), "null_hi": float(null_hi[i, j]),
                "p_value": float(p[i, j]), "q_value": float(q[i, j]), "significant": significant,
                "direction": "above" if observed[i, j] > 1 else "below",
                "verdict": (("shares a region" if observed[i, j] > 1 else "avoids each other") if significant
                            else "not distinguishable from chance"),
            })
    pairs.sort(key=lambda r: (r["q_value"], -abs(np.log(max(r["lift"], 1e-9)))))
    return {
        "test": "relationships", "n_completed": res.n_completed, "stopped_early": res.stopped_early,
        "elapsed_s": res.elapsed_s, "config": _config(s, ctx, n), "results": pairs[:MAX_PAIRS],
        "summary": _summary(s, alpha, len(pairs), sum(r["significant"] for r in pairs), a_vals, b_vals, has_a, has_b,
                            a_lab, b_lab),
    }


def _config(s: RelationshipsInput, ctx: RunContext, n: int) -> dict[str, Any]:
    return {"n_records": n, "n_nodes": len(s.node_members), "region_lift": s.region_lift,
            "min_label_records": s.min_label_records, "min_label_support": s.min_label_support,
            "n_permutations": ctx.settings.n_sims_perm, "seed": ctx.settings.seed, "alpha": ctx.settings.alpha,
            "null": "circular shift of the labels within each block" if s.blocks is not None
                    else "random re-assignment of the labels to the records"}


def _summary(s: RelationshipsInput, alpha: float, n_pairs: int, n_significant: int, a_vals: list[str], b_vals: list[str],
             has_a: np.ndarray, has_b: np.ndarray, a_lab: np.ndarray, b_lab: np.ndarray, note: str | None = None) -> dict[str, Any]:
    tested_a, tested_b = set(a_vals), set(b_vals)
    return {
        "label_a": s.label_a_name, "label_b": s.label_b_name, "alpha": alpha,
        "n_pairs": n_pairs, "n_significant": n_significant, "note": note,
        "without_footprint": {"a": [v for v, ok in zip(a_vals, has_a, strict=True) if not ok],
                              "b": [v for v, ok in zip(b_vals, has_b, strict=True) if not ok]},
        "excluded_labels": sorted({str(v) for v in np.unique(np.concatenate([a_lab, b_lab]))} - tested_a - tested_b),
    }
