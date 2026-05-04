"""JobKind registry + ScheduleSpec helper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from legalbot.db.models import ScheduledJob

JobHandler = Callable[[AsyncSession, ScheduledJob], Awaitable[dict[str, Any] | None]]


@dataclass(slots=True)
class ScheduleSpec:
    kind: Literal["one_shot", "cron", "interval"]
    run_at: datetime | None = None
    cron_expression: str | None = None
    interval_seconds: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.run_at is not None:
            out["run_at"] = self.run_at.isoformat()
        if self.cron_expression is not None:
            out["expression"] = self.cron_expression
        if self.interval_seconds is not None:
            out["interval_seconds"] = self.interval_seconds
        if self.extra:
            out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScheduleSpec:
        return cls(
            kind=raw["kind"],
            run_at=datetime.fromisoformat(raw["run_at"]) if raw.get("run_at") else None,
            cron_expression=raw.get("expression"),
            interval_seconds=raw.get("interval_seconds"),
            extra={
                k: v
                for k, v in raw.items()
                if k not in {"kind", "run_at", "expression", "interval_seconds"}
            },
        )


JOB_KIND_REGISTRY: dict[str, JobHandler] = {}


def register_kind(kind: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        JOB_KIND_REGISTRY[kind] = fn
        return fn

    return decorator
