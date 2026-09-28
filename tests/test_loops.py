import threading

import numpy as np
import pytest

from mc_service.contract import LoopsInput, SimSettings
from mc_service.live import LiveFeed
from mc_service.serialize import to_jsonable
from mc_service.simulations.base import RunContext
from mc_service.simulations.loops import h1_diagram, run


def _circle(n=600, noise=0.08, seed=0):
    rng = np.random.default_rng(seed)
    t = rng.uniform(0, 2 * np.pi, n)
    X = np.column_stack([np.cos(t), np.sin(t), np.zeros(n)])
    return X + rng.normal(scale=noise, size=X.shape)


def _blob(n=600, seed=0):
    rng = np.random.default_rng(seed)
    g = rng.normal(size=(n, 3))
    return np.column_stack([g[:, 0], 0.8 * g[:, 0] + 0.6 * g[:, 1], 0.4 * g[:, 2]])


def _ctx(n_sims=39, seed=42, live=None):
    return RunContext(settings=SimSettings(n_sims=n_sims, seed=seed), n_jobs=1,
                      cancel=threading.Event(), progress=lambda d, t: None, live=live or LiveFeed())


def _run(X, null="gaussian", **kw):
    section = LoopsInput(X=X.tolist(), sample_n=200, null=null, observed_noise_threshold=0.1)
    return to_jsonable(run(section, _ctx(**kw)))    # as the job layer returns it


def test_h1_diagram_sorted_longest_first():
    births, deaths, pers = h1_diagram(_circle(300))
    assert pers.size >= 1
    assert np.all(np.diff(pers) <= 0)
    np.testing.assert_allclose(deaths - births, pers)


def test_noisy_circle_has_exactly_one_significant_loop():
    res = _run(_circle())
    s = res["summary"]
    assert s["n_significant"] == 1
    assert res["results"][0]["significant"]
    assert res["results"][0]["p_value"] == pytest.approx(1 / 40)   # beats every surrogate
    assert s["top_persistence"] > s["noise_band"]


@pytest.mark.parametrize("null", ["gaussian", "shuffle"])
def test_blob_has_no_significant_loop(null):
    assert _run(_blob(), null=null)["summary"]["n_significant"] == 0


def test_result_is_deterministic_and_seed_dependent():
    a, b = _run(_circle()), _run(_circle())
    assert a["summary"] == b["summary"] and a["results"] == b["results"]
    c = _run(_circle(), seed=7)
    assert c["summary"]["null_hist"] != a["summary"]["null_hist"]


def test_significance_is_p_at_most_alpha():
    # 19 simulations: the smallest p is 1/20 = 0.05 — still significant at α = 0.05
    res = _run(_circle(), n_sims=19)
    assert res["results"][0]["p_value"] == pytest.approx(0.05)
    assert res["results"][0]["significant"]


def test_result_shape_and_config():
    res = _run(_circle(), n_sims=19)
    assert res["test"] == "loops" and res["n_completed"] == 19 and not res["stopped_early"]
    assert res["config"] == {"null": "gaussian", "sample_n": 200, "n_points": 600, "dims": 3,
                             "n_sims": 19, "seed": 42, "alpha": 0.05}
    s = res["summary"]
    assert s["heuristic_threshold"] == 0.1
    assert sum(s["null_hist"]["counts"]) == 19
    assert set(s["null_max_quantiles"]) == {"p50", "p90", "p95", "p99"}


def test_live_feed_streams_null_and_surrogate():
    feed = LiveFeed()
    res = _run(_circle(), n_sims=25, live=feed)
    snap = feed.snapshot()
    assert snap["n_null"] == 25 and len(snap["null"]) == 25
    assert snap["observed"]["stat"] == pytest.approx(res["summary"]["top_persistence"])
    assert len(snap["observed"]["points"][0]) == 3
    assert snap["frame"]["index"] == 24 and len(snap["frame"]["points"]) == 200
    assert snap["running"]["p_value"] == pytest.approx(res["results"][0]["p_value"])
    later = feed.snapshot(since=20)
    assert later["observed"] is None and len(later["null"]) == 5 and later["null_from"] == 20


def test_two_dimensional_input_is_padded_for_the_viewer():
    X = _circle()[:, :2]
    feed = LiveFeed()
    section = LoopsInput(X=X.tolist(), sample_n=100)
    run(section, _ctx(n_sims=19, live=feed))
    assert all(p[2] == 0 for p in feed.snapshot()["observed"]["points"])
