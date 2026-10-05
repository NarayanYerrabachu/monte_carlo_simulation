import threading

import numpy as np
import pytest

from mc_service.contract import RelationshipsInput, SimSettings
from mc_service.live import LiveFeed
from mc_service.serialize import to_jsonable
from mc_service.simulations.base import RunContext
from mc_service.simulations.relationships import run


def _data(seed=0, linked=True, n=1200):
    """Six Mapper groups of 200 records. Label A "north" lives in groups 0–1, label B "wet" in
    groups 0–1 too when ``linked`` (they share a region), in groups 3–4 otherwise (different regions)."""
    rng = np.random.default_rng(seed)
    group = np.repeat(np.arange(6), n // 6)
    a = np.where(np.isin(group, (0, 1)) & (rng.random(n) < 0.9), "north", "south")
    b_groups = (0, 1) if linked else (3, 4)
    b = np.where(np.isin(group, b_groups) & (rng.random(n) < 0.9), "wet", "dry")
    return {"labels_a": a.tolist(), "labels_b": b.tolist(),
            "node_members": [np.flatnonzero(group == g).tolist() for g in range(6)]}


def _run(section, n_perm=199, **kw):
    ctx = RunContext(settings=SimSettings(n_sims_perm=n_perm, seed=3), n_jobs=1, cancel=threading.Event(),
                     progress=lambda d, t: None, live=LiveFeed())
    return to_jsonable(run(RelationshipsInput(**section, **kw), ctx))


def test_labels_sharing_a_region_are_significant():
    res = _run(_data(linked=True), label_a_name="Depot", label_b_name="Weather")
    pair = next(r for r in res["results"] if (r["a"], r["b"]) == ("north", "wet"))
    assert pair["lift"] > 2 and pair["significant"] and pair["q_value"] <= 0.05
    assert pair["verdict"] == "shares a region" and pair["null_lo"] < 1 < pair["null_hi"]
    assert pair["lift"] > pair["null_hi"]
    s = res["summary"]
    assert s["label_a"] == "Depot" and s["n_significant"] >= 1 and res["n_completed"] == 199


def test_labels_in_different_regions_avoid_each_other():
    res = _run(_data(linked=False))
    pair = next(r for r in res["results"] if (r["a"], r["b"]) == ("north", "wet"))
    assert pair["lift"] < 0.2 and pair["significant"] and pair["verdict"] == "avoids each other"


def test_unrelated_labels_are_not_significant():
    """B's labels are random with respect to A: no pair survives the false-discovery correction."""
    d = _data(linked=True)
    rng = np.random.default_rng(5)
    group = np.repeat(np.arange(6), 200)
    # "wet" concentrates in groups 2–3 for half of the records chosen at random, independent of A within the shape
    d["labels_a"] = np.where(rng.random(1200) < 0.3, "north", "south").tolist()
    d["labels_b"] = np.where(np.isin(group, (2, 3)) & (rng.random(1200) < 0.9), "wet", "dry").tolist()
    res = _run(d)
    assert res["summary"]["n_significant"] == 0 or all(not r["significant"] for r in res["results"])


def test_block_shift_keeps_runs_and_is_deterministic():
    d = _data(linked=True)
    blocks = ["T1"] * 600 + ["T2"] * 600
    a, b = _run(d, blocks=blocks, order=list(range(600)) * 2), _run(d, blocks=blocks, order=list(range(600)) * 2)
    assert a["results"] == b["results"] and "circular shift" in a["config"]["null"]
    assert "random re-assignment" in _run(d)["config"]["null"]


def test_rare_and_placeholder_labels_are_left_out():
    d = _data()
    d["labels_a"][:5] = ["rare"] * 5
    d["labels_b"][:30] = ["N/A"] * 30
    s = _run(d)["summary"]
    assert "rare" in s["excluded_labels"] and "N/A" in s["excluded_labels"]


def test_nothing_to_test_is_reported():
    d = _data()
    d["labels_b"] = ["same"] * 1200                      # one label everywhere: no footprint
    res = _run(d)
    assert res["results"] == [] and res["summary"]["n_pairs"] == 0 and "nothing to test" in res["summary"]["note"]
    assert res["summary"]["without_footprint"]["b"] == ["same"]


def test_p_values_stay_in_range():
    res = _run(_data(linked=True), n_perm=99)
    assert all(1 / 100 <= r["p_value"] <= 1 and r["p_value"] <= r["q_value"] <= 1 for r in res["results"])
    assert pytest.approx(min(r["p_value"] for r in res["results"])) == 0.02      # two-sided, +1 correction
