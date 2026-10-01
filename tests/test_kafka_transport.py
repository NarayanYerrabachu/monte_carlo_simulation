"""Kafka transport with a fake producer / consumer — no broker needed."""
import gzip
import json
import time

import pytest

from mc_service.jobs import JobStore
from mc_service.kafka_transport import TOPIC_STATUS, KafkaWorker
from mc_service.simulations import RUNNERS


class FakeProducer:
    def __init__(self):
        self.events = []

    def produce(self, topic, key, value):
        assert topic == TOPIC_STATUS
        self.events.append((key.decode(), json.loads(value)))

    def poll(self, timeout):
        pass

    def flush(self, timeout):
        pass


def _request(**overrides):
    req = {"contract_version": "1", "dataset_id": "ds-1", "settings": {"n_sims": 20, "seed": 1},
           "loops": {"X": [[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]], "sample_n": 50}}
    req.update(overrides)
    return req


def _payload(tmp_path, job_id, body=None):
    path = tmp_path / f"{job_id}.json.gz"
    path.write_bytes(gzip.compress(json.dumps(body or _request()).encode()))
    return path


def _message(job_id, path):
    return json.dumps({"contract_version": "1", "job_id": job_id, "dataset_id": "ds-1",
                       "tests": ["loops"], "payload_path": str(path)}).encode()


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setitem(RUNNERS, "loops", lambda section, ctx: {
        "test": "loops", "n_completed": 1, "stopped_early": False, "elapsed_s": 0.0,
        "config": {}, "results": [], "summary": {}})
    store = JobStore(workers=1, n_jobs=1, ttl_s=60)
    producer = FakeProducer()
    worker = KafkaWorker(store, "unused:9092", producer=producer)
    yield store, producer, worker
    store.shutdown()


def _wait_done(store, job_id):
    end = time.time() + 10
    while store.get(job_id).status in ("queued", "running") and time.time() < end:
        time.sleep(0.02)


def test_request_runs_under_the_senders_job_id(setup, tmp_path):
    store, producer, worker = setup
    path = _payload(tmp_path, "abc123")
    worker.handle_request(_message("abc123", path))
    assert store.get("abc123") is not None and not path.exists()      # hand-over file removed
    assert producer.events[0][0] == "abc123"                           # status published as soon as it is queued
    _wait_done(store, "abc123")
    worker.publish_changes()
    key, final = producer.events[-1]
    assert key == "abc123" and final["status"] == "done" and final["errors"] == {}
    n = len(producer.events)
    worker.publish_changes()                                          # nothing new, nothing published
    assert len(producer.events) == n


def test_redelivered_request_is_not_run_twice(setup, tmp_path):
    store, producer, worker = setup
    worker.handle_request(_message("dup", _payload(tmp_path, "dup")))
    worker.handle_request(_message("dup", _payload(tmp_path, "dup")))
    assert len(store.list_jobs()) == 1


@pytest.mark.parametrize("body", [{"contract_version": "1"}, "not a gzip file"])
def test_unreadable_or_invalid_request_reports_failed(setup, tmp_path, body):
    _, producer, worker = setup
    path = tmp_path / "bad.json.gz"
    if isinstance(body, dict):
        path.write_bytes(gzip.compress(json.dumps(body).encode()))
    else:
        path.write_bytes(b"garbage")
    worker.handle_request(_message("bad", path))
    (key, event), = producer.events
    assert key == "bad" and event["status"] == "failed" and "request" in event["errors"]


def test_missing_payload_reports_failed(setup, tmp_path):
    _, producer, worker = setup
    worker.handle_request(_message("gone", tmp_path / "nope.json.gz"))
    assert producer.events[0][1]["status"] == "failed"


def test_malformed_messages_are_ignored(setup):
    store, producer, worker = setup
    worker.handle_request(b"not json")
    worker.handle_request(json.dumps({"no": "job_id"}).encode())
    worker.handle_cancel(b"???")
    assert store.list_jobs() == [] and producer.events == []


def test_cancel_stops_a_queued_job(setup, tmp_path, monkeypatch):
    store, producer, worker = setup
    import threading
    gate = threading.Event()
    monkeypatch.setitem(RUNNERS, "loops", lambda section, ctx: gate.wait(5) or {})
    worker.handle_request(_message("first", _payload(tmp_path, "first")))     # occupies the only worker
    worker.handle_request(_message("second", _payload(tmp_path, "second")))   # waits in the queue
    worker.handle_cancel(json.dumps({"job_id": "second"}).encode())
    assert store.get("second").status == "cancelled"
    worker.publish_changes()
    assert producer.events[-1][1]["status"] == "cancelled"
    gate.set()


def test_start_requires_bootstrap_servers():
    with pytest.raises(RuntimeError, match="KAFKA_BOOTSTRAP_SERVERS"):
        KafkaWorker(None, "").start()
