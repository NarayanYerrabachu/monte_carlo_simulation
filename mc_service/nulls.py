"""Null-model generators: surrogate data and label permutations.

Each takes the data and a ``numpy.random.Generator`` and returns a new array;
inputs are never modified.
"""
from __future__ import annotations

import numpy as np


def gaussian_surrogate(X: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Same mean and covariance as ``X``, otherwise structureless (an ellipsoid blob)."""
    X = np.asarray(X, dtype=float)
    mean = X.mean(axis=0)
    cov = np.atleast_2d(np.cov(X, rowvar=False))
    return rng.multivariate_normal(mean, cov, size=len(X), method="svd")


def column_shuffle(X: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Each column permuted independently: marginals kept, dependence between columns destroyed."""
    X = np.asarray(X)
    return np.column_stack([rng.permutation(X[:, j]) for j in range(X.shape[1])])


def permute(values: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Plain random permutation."""
    return rng.permutation(np.asarray(values))


def circular_shift_within_blocks(values: np.ndarray, blocks: np.ndarray,
                                 order: np.ndarray | None, rng: np.random.Generator) -> np.ndarray:
    """Rotate ``values`` by a random offset inside each block (e.g. ticker), along ``order``.

    Keeps each block's values and their runs (regimes lasting several days)
    intact, so time autocorrelation cannot fake significance; only the
    alignment with everything else is broken.
    """
    values = np.asarray(values)
    blocks = np.asarray(blocks)
    order = np.arange(len(values)) if order is None else np.asarray(order)
    out = values.copy()
    for b in np.unique(blocks):
        idx = np.flatnonzero(blocks == b)
        idx = idx[np.argsort(order[idx], kind="stable")]
        if len(idx) > 1:
            out[idx] = np.roll(values[idx], int(rng.integers(len(idx))))
    return out
