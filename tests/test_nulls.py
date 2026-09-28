import numpy as np

from mc_service.nulls import (
    circular_shift_within_blocks,
    column_shuffle,
    gaussian_surrogate,
    permute,
)


def test_gaussian_surrogate_keeps_mean_and_covariance():
    rng = np.random.default_rng(0)
    X = rng.multivariate_normal([1, -2], [[2.0, 1.2], [1.2, 1.0]], size=5000)
    S = gaussian_surrogate(X, np.random.default_rng(1))
    assert S.shape == X.shape
    np.testing.assert_allclose(S.mean(0), X.mean(0), atol=0.1)
    np.testing.assert_allclose(np.cov(S, rowvar=False), np.cov(X, rowvar=False), atol=0.15)


def test_gaussian_surrogate_singular_covariance():
    X = np.column_stack([np.arange(100.0), 2 * np.arange(100.0)])   # rank 1
    S = gaussian_surrogate(X, np.random.default_rng(0))
    assert np.isfinite(S).all()


def test_column_shuffle_keeps_marginals_breaks_dependence():
    rng = np.random.default_rng(0)
    x = rng.normal(size=2000)
    X = np.column_stack([x, x])
    S = column_shuffle(X, np.random.default_rng(1))
    for j in range(2):
        np.testing.assert_array_equal(np.sort(S[:, j]), np.sort(X[:, j]))
    assert abs(np.corrcoef(S[:, 0], S[:, 1])[0, 1]) < 0.1


def test_permute_does_not_modify_input():
    a = np.arange(10)
    p = permute(a, np.random.default_rng(0))
    np.testing.assert_array_equal(a, np.arange(10))
    np.testing.assert_array_equal(np.sort(p), a)


def test_circular_shift_keeps_block_contents_and_runs():
    values = np.array(["A", "A", "B", "B", "B", "x", "y", "z"])
    blocks = np.array(["t1"] * 5 + ["t2"] * 3)
    order = np.array([0, 1, 2, 3, 4, 2, 0, 1])      # t2 time order: y, z, x
    out = circular_shift_within_blocks(values, blocks, order, np.random.default_rng(3))
    assert sorted(out[:5]) == sorted(values[:5])
    assert sorted(out[5:]) == sorted(values[5:])
    # t1 is a rotation of AABBB → still exactly one run of each label (cyclically)
    seq = "".join(out[:5])
    assert seq in {"AABBB", "ABBBA", "BBBAA", "BBAAB", "BAABB"}
    # t2 along its time order is a rotation of y, z, x
    t2 = out[5:][np.argsort(order[5:])]
    assert "".join(t2) in {"yzx", "zxy", "xyz"}
