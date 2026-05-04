"""Prometheus metrics primitives."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry(auto_describe=True)

job_state_gauge = Gauge(
    "legalbot_job_state",
    "Number of processing_job rows in each state",
    labelnames=("state",),
    registry=registry,
)
job_dispatch_latency = Histogram(
    "legalbot_job_dispatch_latency_seconds",
    "Time from ready→dispatched transition",
    registry=registry,
)
job_runtime_seconds = Histogram(
    "legalbot_job_runtime_seconds",
    "processing→completed runtime",
    registry=registry,
)

artifact_writes_total = Counter(
    "legalbot_artifact_writes_total",
    "Artifacts written",
    labelnames=("kind",),
    registry=registry,
)
artifact_bytes = Counter(
    "legalbot_artifact_bytes_total",
    "Total artifact bytes written",
    labelnames=("storage",),
    registry=registry,
)
artifact_version_depth = Histogram(
    "legalbot_artifact_version_depth",
    "Version depth observed on write",
    registry=registry,
)

interrupts_total = Counter(
    "legalbot_interrupts_total",
    "Interrupts raised",
    labelnames=("kind",),
    registry=registry,
)
interrupt_resolutions_total = Counter(
    "legalbot_interrupt_resolutions_total",
    "Interrupt resolutions",
    labelnames=("kind", "decision"),
    registry=registry,
)
