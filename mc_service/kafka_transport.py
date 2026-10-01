"""Kafka transport: consume jobs from CortXplorer and publish their status (``MC_TRANSPORT=kafka``).

The HTTP job API keeps working; this adds a second way in. Topics (same contract as the demo's
``backend/services/mc_kafka.py``):

    mc.jobs.requested   key dataset_id  {contract_version, job_id, dataset_id, tests, payload_path, ...}  (consumed)
    mc.jobs.cancel      key job_id      {job_id}                                                           (consumed)
    mc.jobs.status      key job_id      {job_id, status, progress, errors}                                 (published)

The request itself is too large for a message: ``payload_path`` points to a gzipped JSON file on a
volume shared with the sender (``MC_PAYLOAD_DIR``). The job runs under the sender's ``job_id`` so
the live viewer link ``/?job=<job_id>`` works. A request that cannot be read or validated is
reported as a ``failed`` status; the offset is committed only after the job is queued.

    KAFKA_BOOTSTRAP_SERVERS   e.g. kafka-1:19092,kafka-2:19092,kafka-3:19092
    MC_PAYLOAD_DIR            directory the payload files are read from (and removed after loading)
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from mc_service.contract import SimulationRequest
from mc_service.jobs import ACTIVE, JobStore

log = logging.getLogger(__name__)

TOPIC_REQUESTED = "mc.jobs.requested"
TOPIC_CANCEL = "mc.jobs.cancel"
TOPIC_STATUS = "mc.jobs.status"
GROUP_ID = "mc-service"
STATUS_INTERVAL_S = 1.0


class KafkaWorker:
    """Consumes requests and cancels, publishes status. Producer / consumers are injectable for tests."""

    def __init__(self, store: JobStore, servers: str, producer: Any = None,
                 consumer_factory: Callable[[str, str, list[str], str], Any] | None = None):
        self._store = store
        self._servers = servers
        self._producer = producer
        self._consumer_factory = consumer_factory
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._tracked: dict[str, tuple] = {}          # job_id -> last published (status, progress, errors)
        self._lock = threading.Lock()

    # ---- lifecycle ----
    def start(self) -> None:
        if not self._servers and self._producer is None:
            raise RuntimeError("MC_TRANSPORT=kafka needs KAFKA_BOOTSTRAP_SERVERS")
        # Requests: one shared group, so several service instances share the work.
        # Cancels: a group per instance, so the instance running the job sees it.
        loops = [(self._requests_loop, GROUP_ID, [TOPIC_REQUESTED], "earliest"),
                 (self._cancel_loop, f"{GROUP_ID}-cancel-{uuid.uuid4().hex[:8]}", [TOPIC_CANCEL], "latest")]
        for target, group, topics, reset in loops:
            consumer = self._make_consumer(group, topics, reset)
            thread = threading.Thread(target=target, args=(consumer,), daemon=True, name=f"mc-kafka-{topics[0]}")
            thread.start()
            self._threads.append(thread)
        thread = threading.Thread(target=self._status_loop, daemon=True, name="mc-kafka-status")
        thread.start()
        self._threads.append(thread)
        log.info("Kafka transport on: consuming %s, %s; publishing %s", TOPIC_REQUESTED, TOPIC_CANCEL, TOPIC_STATUS)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        if self._producer is not None:
            self._producer.flush(5)

    def _make_consumer(self, group: str, topics: list[str], reset: str) -> Any:
        if self._consumer_factory is not None:
            return self._consumer_factory(group, self._servers, topics, reset)
        from confluent_kafka import Consumer

        consumer = Consumer({"bootstrap.servers": self._servers, "group.id": group, "auto.offset.reset": reset,
                             "enable.auto.commit": False, "max.poll.interval.ms": 600000})
        consumer.subscribe(topics)
        return consumer

    def _get_producer(self) -> Any:
        if self._producer is None:
            from confluent_kafka import Producer

            self._producer = Producer({"bootstrap.servers": self._servers, "acks": "all", "enable.idempotence": True})
        return self._producer

    # ---- consuming ----
    def _poll_loop(self, consumer: Any, handle: Callable[[Any], None]) -> None:
        try:
            while not self._stop.is_set():
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    log.warning("Kafka consumer error: %s", msg.error())
                    continue
                try:
                    handle(msg)
                except Exception:                      # a bad message must not kill the loop
                    log.exception("Kafka: could not handle a message on %s", msg.topic())
                consumer.commit(message=msg, asynchronous=False)
        finally:
            consumer.close()

    def _requests_loop(self, consumer: Any) -> None:
        self._poll_loop(consumer, lambda msg: self.handle_request(msg.value()))

    def _cancel_loop(self, consumer: Any) -> None:
        self._poll_loop(consumer, lambda msg: self.handle_cancel(msg.value()))

    def handle_request(self, raw: bytes | str | None) -> None:
        try:
            message = json.loads(raw or b"")
            job_id, path = str(message["job_id"]), Path(message["payload_path"])
        except (ValueError, KeyError, TypeError):
            log.warning("Kafka: ignoring malformed job request")
            return
        if self._store.get(job_id) is not None:       # redelivery after a crash: already queued
            return
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                request = SimulationRequest.model_validate(json.load(fh))
        except (OSError, ValueError, ValidationError) as exc:
            log.warning("Kafka: job %s rejected: %s", job_id, exc)
            self._publish(job_id, {"job_id": job_id, "status": "failed", "progress": None,
                                   "errors": {"request": f"{type(exc).__name__}: {exc}"[:500]}})
            return
        try:
            path.unlink(missing_ok=True)               # the file is only a hand-over; drop it
        except OSError:
            pass
        job = self._store.submit(request, job_id=job_id)
        with self._lock:
            self._tracked[job.id] = ()
        self._publish_status(job.id)

    def handle_cancel(self, raw: bytes | str | None) -> None:
        try:
            job_id = str(json.loads(raw or b"")["job_id"])
        except (ValueError, KeyError, TypeError):
            return
        if self._store.get(job_id) is not None:
            self._store.cancel(job_id)

    # ---- publishing ----
    def _status_loop(self) -> None:
        while not self._stop.wait(STATUS_INTERVAL_S):
            self.publish_changes()

    def publish_changes(self) -> None:
        with self._lock:
            ids = list(self._tracked)
        for job_id in ids:
            self._publish_status(job_id)

    def _publish_status(self, job_id: str) -> None:
        job = self._store.get(job_id)
        if job is None:                                # expired
            with self._lock:
                self._tracked.pop(job_id, None)
            return
        view = job.status_view()
        event = {"job_id": job_id, "status": view["status"], "progress": view["progress"], "errors": view["errors"]}
        signature = (view["status"], json.dumps(view["progress"], sort_keys=True), json.dumps(view["errors"], sort_keys=True))
        with self._lock:
            if self._tracked.get(job_id) == signature:
                return
            self._tracked[job_id] = signature
            if view["status"] not in ACTIVE:           # final status goes out once, then stop tracking
                self._tracked.pop(job_id, None)
        self._publish(job_id, event)

    def _publish(self, job_id: str, event: dict[str, Any]) -> None:
        try:
            producer = self._get_producer()
            producer.produce(TOPIC_STATUS, key=job_id.encode(), value=json.dumps(event).encode())
            producer.poll(0)
        except Exception:
            log.exception("Kafka: could not publish status of job %s", job_id)
