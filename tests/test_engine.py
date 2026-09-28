import threading
import time

import numpy as np
import pytest

from mc_service.engine import (
    Cancelled,
    SimConfig,
    bh_qvalues,
    p_value,
    p_values,
    percentile_ci,
    simulate,
)


def _normal_mean_draw():
    def draw(rng):
        return rng.normal(size=50).mean()
    return draw


def test_simulate_is_deterministic_and_independent_of_workers():
    draw = _normal_mean_draw()
    a = simulate(draw, SimConfig(n_sims=70, seed=7))
    b = simulate(draw, SimConfig(n_sims=70, seed=7))
    c = simulate(draw, SimConfig(n_sims=70, seed=7, n_jobs=2))
    assert a.n_completed == 70 and not a.stopped_early
    np.testing.assert_array_equal(a.null, b.null)
    np.testing.assert_array_equal(a.null, c.null)


def test_simulate_different_seed_differs():
    draw = _normal_mean_draw()
    a = simulate(draw, SimConfig(n_sims=40, seed=1))
    b = simulate(draw, SimConfig(n_sims=40, seed=2))
    assert not np.array_equal(a.null, b.null)


def test_simulate_vector_statistic_shape():
    res = simulate(lambda rng: rng.normal(size=3), SimConfig(n_sims=20, seed=0))
    assert res.null.shape == (20, 3)


def test_simulate_reports_progress():
    calls = []
    simulate(lambda rng: 0.0, SimConfig(n_sims=70, seed=0, progress=lambda d, t: calls.append((d, t))))
    assert calls[-1] == (70, 70)
    assert [d for d, _ in calls] == sorted(d for d, _ in calls)


def test_simulate_time_budget_stops_early():
    def slow(rng):
        time.sleep(0.01)
        return 0.0
    res = simulate(slow, SimConfig(n_sims=500, seed=0, max_seconds=0.05))
    assert res.stopped_early
    assert 0 < res.n_completed < 500
    assert res.null.shape[0] == res.n_completed


def test_simulate_cancel_raises():
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        simulate(lambda rng: 0.0, SimConfig(n_sims=10, seed=0, cancel=ev))


def test_p_value_plus_one_correction():
    null = np.arange(99, dtype=float)             # 0..98
    assert p_value(1000.0, null) == pytest.approx(1 / 100)   # never 0
    assert p_value(-1.0, null) == pytest.approx(1.0)
    assert p_value(49.0, null) == pytest.approx((1 + 50) / 100)
    assert p_value(-1.0, null, tail="less") == pytest.approx(1 / 100)


def test_p_value_two_sided_and_undefined():
    null = np.arange(99, dtype=float)
    assert p_value(1000.0, null, tail="two-sided") == pytest.approx(2 / 100)
    assert p_value(49.0, null, tail="two-sided") == pytest.approx(1.0)
    assert p_value(None, null) is None
    assert p_value(1.0, np.array([])) is None
    with pytest.raises(ValueError):
        p_values(1.0, null, tail="up")


def test_p_values_vectorised_ignores_nan_simulations():
    null = np.array([[1.0, np.nan], [2.0, np.nan], [3.0, 5.0]])
    p = p_values([2.5, 4.0], null)
    assert p[0] == pytest.approx((1 + 1) / (1 + 3))
    assert p[1] == pytest.approx((1 + 1) / (1 + 1))
    assert np.isnan(p_values([np.nan], null[:, :1])[0])


def test_bh_qvalues_hand_example():
    p = np.array([0.01, 0.04, 0.03, 0.20])
    # sorted 0.01,0.03,0.04,0.20 → ×4/rank = 0.04,0.06,0.0533,0.20 → monotone 0.04,0.0533,0.0533,0.20
    q = bh_qvalues(p)
    np.testing.assert_allclose(q, [0.04, 0.16 / 3, 0.16 / 3, 0.20])


def test_bh_qvalues_nan_and_cap():
    q = bh_qvalues([0.9, np.nan, 0.8])
    assert np.isnan(q[1])
    assert q[0] <= 1 and q[2] <= 1
    assert q[0] == pytest.approx(0.9) and q[2] == pytest.approx(0.9)


def test_percentile_ci():
    lo, hi = percentile_ci(np.arange(101), 0.9)
    assert (lo, hi) == pytest.approx((5.0, 95.0))
    assert percentile_ci([np.nan]) is None
