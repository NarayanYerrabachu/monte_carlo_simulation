"""Request / response contract between the CortXplorer demo and this service.

``contract_version`` "1". The demo sends the data it already computed (feature
matrices, labels, Mapper membership, timeline, scores) plus every parameter
the re-implemented statistics need — the service has no hidden defaults that
could drift from the demo. Per-record arrays are index-aligned within a
section. Unavailable values are ``null``.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mc_service import CONTRACT_VERSION

TEST_NAMES = ("loops", "relationships", "pre_event", "anomaly_stability", "mapper_stability", "fleet")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _rect(X: list[list[float]], name: str, min_cols: int = 1, max_cols: int | None = None) -> int:
    """Check X is a non-empty rectangular matrix; return its column count."""
    if not X:
        raise ValueError(f"{name} is empty")
    width = len(X[0])
    if any(len(row) != width for row in X):
        raise ValueError(f"{name} rows have different lengths")
    if width < min_cols or (max_cols is not None and width > max_cols):
        raise ValueError(f"{name} must have {min_cols}..{max_cols or '∞'} columns, got {width}")
    return width


def _same_len(n: int, **arrays: list | None) -> None:
    for name, arr in arrays.items():
        if arr is not None and len(arr) != n:
            raise ValueError(f"{name} has {len(arr)} entries, expected {n}")


def _members_in_range(node_members: list[list[int]], n: int) -> None:
    for k, members in enumerate(node_members):
        if any(i < 0 or i >= n for i in members):
            raise ValueError(f"node_members[{k}] has a record index outside 0..{n - 1}")


class SimSettings(_Model):
    n_sims: int = Field(200, ge=19, le=100_000)          # ≥ 19 so p can reach 0.05 (significant: p ≤ α)
    n_sims_perm: int = Field(999, ge=19, le=100_000)     # relationship permutations (BH needs small p)
    seed: int = Field(42, ge=0)
    alpha: float = Field(0.05, gt=0, le=0.5)
    max_seconds: float | None = Field(600, gt=0)      # per test; None = no budget


class LoopsInput(_Model):
    X: list[list[float]]                                  # PCA projection used by the demo (≤ 10 dims)
    sample_n: int = Field(1000, ge=50, le=1500)           # points per ripser run (observed and null alike)
    null: Literal["gaussian", "shuffle"] = "gaussian"
    observed_noise_threshold: float | None = None      # the demo's heuristic band, echoed for comparison

    @model_validator(mode="after")
    def _check(self):
        _rect(self.X, "X", 1, 10)
        return self


class RelationshipsInput(_Model):
    labels_a: list[str]
    labels_b: list[str]
    node_members: list[list[int]]                         # Mapper membership (record positions)
    region_lift: float = Field(2.0, gt=0)
    min_label_records: int = Field(2, ge=1)
    blocks: list[str] | None = None                    # ticker per record → circular shift within block
    order: list[int] | None = None                     # time order within block

    @model_validator(mode="after")
    def _check(self):
        n = len(self.labels_a)
        if n == 0:
            raise ValueError("labels_a is empty")
        _same_len(n, labels_b=self.labels_b, blocks=self.blocks, order=self.order)
        if not self.node_members:
            raise ValueError("node_members is empty")
        _members_in_range(self.node_members, n)
        if self.order is not None and self.blocks is None:
            raise ValueError("order needs blocks")
        return self


class PreEventInput(_Model):
    ticker: list[str]
    pos_in_ticker: list[int]
    event_rows: list[int]                                 # record positions of the real events
    Z: list[list[float]]                                  # standardised features
    feature_names: list[str]
    anomaly: list[float | None]
    high_threshold: float = 0.6
    event_region: list[bool] | None = None             # record lies in the Mapper event region
    bands: list[tuple[int, int]]                          # weeks (hi, lo), e.g. [(6, 4), (4, 2), (2, 0)]
    days_per_week: int = Field(5, ge=1)

    @model_validator(mode="after")
    def _check(self):
        n = len(self.ticker)
        if n == 0:
            raise ValueError("ticker is empty")
        _same_len(n, pos_in_ticker=self.pos_in_ticker, Z=self.Z, anomaly=self.anomaly,
                  event_region=self.event_region)
        if _rect(self.Z, "Z") != len(self.feature_names):
            raise ValueError("Z columns and feature_names differ in length")
        if not self.event_rows:
            raise ValueError("event_rows is empty")
        if any(i < 0 or i >= n for i in self.event_rows):
            raise ValueError(f"event_rows has a record index outside 0..{n - 1}")
        if not self.bands or any(not (hi > lo >= 0) for hi, lo in self.bands):
            raise ValueError("bands must be non-empty (hi, lo) weeks with hi > lo ≥ 0")
        return self


class AnomalyInput(_Model):
    X: list[list[float]]                                  # X_norm used by the demo
    record_ids: list[str]
    cluster_labels: list[int]                             # -1 = noise
    contamination: float = Field(gt=0, le=0.5)
    iso_weight: float = 0.6
    topo_weight: float = 0.4
    high_threshold: float = 0.6
    top_k: int = Field(30, ge=1)

    @model_validator(mode="after")
    def _check(self):
        _rect(self.X, "X")
        _same_len(len(self.X), record_ids=self.record_ids, cluster_labels=self.cluster_labels)
        return self


class MapperInput(_Model):
    X: list[list[float]]
    lens: list[float]
    n_intervals: int = Field(ge=1)
    overlap: float = Field(ge=0, lt=1)
    eps: float = Field(gt=0)                              # the eps actually used (after auto-eps)
    min_samples: int = Field(ge=1)
    perturb: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {"n_intervals": (-2, 2), "overlap": (-0.1, 0.1), "eps": (0.9, 1.1)})
    eval_sample_n: int = Field(2000, ge=50, le=10_000)
    node_ids: list[int]
    node_members: list[list[int]]                         # observed graph to score

    @model_validator(mode="after")
    def _check(self):
        _rect(self.X, "X")
        n = len(self.X)
        _same_len(n, lens=self.lens)
        _same_len(len(self.node_members), node_ids=self.node_ids)
        _members_in_range(self.node_members, n)
        unknown = set(self.perturb) - {"n_intervals", "overlap", "eps"}
        if unknown:
            raise ValueError(f"perturb has unknown keys: {sorted(unknown)}")
        return self


class FleetInput(_Model):
    """Fleet records (one per vehicle and day) with their TDA regime and ML anomaly score."""
    record_id: list[str] | None = None
    vehicle_id: list[str]
    date: list[str]                                        # day of the record; days are sampled as blocks
    regime: list[int]                                      # TDA cluster of the record (-1 = noise)
    anomaly_score: list[float | None] | None = None        # ML anomaly score 0–1
    deliveries_planned: list[float]
    deliveries_completed: list[float]
    deliveries_on_time: list[float]
    route_duration_h: list[float]
    distance_km: list[float]
    fuel_l: list[float]
    fuel_price_eur_l: list[float]
    maintenance_cost_eur: list[float]
    breakdown: list[int]
    driver_available: list[int]
    regime_labels: dict[str, str] | None = None            # cluster id → label from the TDA themes
    fleet_size: int | None = Field(None, ge=1)             # default: number of distinct vehicles
    delivery_target_h: float = Field(4.0, gt=0)
    sla_on_time: float = Field(0.95, gt=0, le=1)           # a day meets the SLA at this on-time share
    breakdown_alert: int | None = Field(None, ge=0)        # report P(breakdowns > alert); default: P90
    exclude_anomalies_above: float | None = Field(0.6, ge=0, le=1)
    context: dict[str, Any] | None = None                  # TDA / ML findings from the sender, shown with the result
    # TDA Mapper graph with its 3D layout: {"nodes": [{id, size, x, y, z, members: [record positions]}],
    # "edges": [[source, target], ...]} — drawn by the live viewer
    mapper: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self):
        n = len(self.vehicle_id)
        if n == 0:
            raise ValueError("vehicle_id is empty")
        _same_len(n, record_id=self.record_id, date=self.date, regime=self.regime, anomaly_score=self.anomaly_score,
                  deliveries_planned=self.deliveries_planned, deliveries_completed=self.deliveries_completed,
                  deliveries_on_time=self.deliveries_on_time, route_duration_h=self.route_duration_h,
                  distance_km=self.distance_km, fuel_l=self.fuel_l, fuel_price_eur_l=self.fuel_price_eur_l,
                  maintenance_cost_eur=self.maintenance_cost_eur, breakdown=self.breakdown,
                  driver_available=self.driver_available)
        if sum(self.deliveries_planned) <= 0:
            raise ValueError("deliveries_planned sums to 0 — nothing to simulate")
        return self


class FleetOverrides(_Model):
    """What-if parameters for re-running a fleet job on the same records."""
    fleet_size: int | None = Field(None, ge=1)
    delivery_target_h: float | None = Field(None, gt=0)
    sla_on_time: float | None = Field(None, gt=0, le=1)
    breakdown_alert: int | None = Field(None, ge=0)
    exclude_anomalies_above: float | None = Field(None, ge=0, le=1)


class RerunRequest(_Model):
    """``POST /v1/jobs/{id}/rerun``: same fleet records, new parameters. Only fields sent are changed;
    send ``exclude_anomalies_above: null`` explicitly to switch the anomaly exclusion off."""
    n_sims: int | None = Field(None, ge=19, le=100_000)
    fleet: FleetOverrides = Field(default_factory=FleetOverrides)


class SimulationRequest(_Model):
    contract_version: Literal["1"]
    dataset_id: str = Field(min_length=1)                # echoed back; the demo drops stale results
    settings: SimSettings = Field(default_factory=SimSettings)
    loops: LoopsInput | None = None
    relationships: RelationshipsInput | None = None
    pre_event: PreEventInput | None = None
    anomaly_stability: AnomalyInput | None = None
    mapper_stability: MapperInput | None = None
    fleet: FleetInput | None = None

    @model_validator(mode="after")
    def _check(self):
        if not self.requested_tests():
            raise ValueError(f"select at least one test: {', '.join(TEST_NAMES)}")
        return self

    def requested_tests(self) -> list[str]:
        return [t for t in TEST_NAMES if getattr(self, t) is not None]


class TestResult(_Model):
    test: str
    n_completed: int
    stopped_early: bool
    elapsed_s: float
    config: dict[str, Any]
    results: list[dict[str, Any]]
    summary: dict[str, Any]


class SimulationResponse(_Model):
    contract_version: str = CONTRACT_VERSION
    dataset_id: str
    job_id: str
    loops: TestResult | None = None
    relationships: TestResult | None = None
    pre_event: TestResult | None = None
    anomaly_stability: TestResult | None = None
    mapper_stability: TestResult | None = None
    fleet: TestResult | None = None
    errors: dict[str, str] = Field(default_factory=dict)  # per test that failed: why
