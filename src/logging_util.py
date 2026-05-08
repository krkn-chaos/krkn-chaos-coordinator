"""Structured JSON logging for pipeline phases."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PhaseLogEntry:
    """A single structured log entry for a pipeline phase."""

    phase: str
    status: str
    summary: str
    bug_key: str = ""
    model: str = ""
    tokens: int = 0
    elapsed_sec: float = 0.0
    confidence: float = 0.0
    retries: int = 0
    cache_hit: bool = False
    extra: dict | None = None


class StructuredLogger:
    """Emits structured JSON log entries for pipeline phases."""

    def __init__(self, name: str = "coordinator") -> None:
        self._logger = logging.getLogger(name)
        self._entries: list[PhaseLogEntry] = []

    def log_phase(
        self,
        phase: str,
        status: str,
        summary: str,
        **kwargs: object,
    ) -> None:
        entry = PhaseLogEntry(phase=phase, status=status, summary=summary, **kwargs)
        self._entries = [*self._entries, entry]
        log_dict = {k: v for k, v in asdict(entry).items() if v}
        self._logger.info(json.dumps(log_dict))

    def get_entries(self, phase: str | None = None) -> list[PhaseLogEntry]:
        if phase is None:
            return list(self._entries)
        return [e for e in self._entries if e.phase == phase]

    def count_by_status(self, phase: str | None = None) -> dict[str, int]:
        entries = self.get_entries(phase)
        counts: dict[str, int] = {}
        for e in entries:
            counts = {**counts, e.status: counts.get(e.status, 0) + 1}
        return counts

    def total_tokens(self) -> int:
        return sum(e.tokens for e in self._entries)

    def total_retries(self) -> int:
        return sum(e.retries for e in self._entries)

    def clear(self) -> None:
        self._entries = []
