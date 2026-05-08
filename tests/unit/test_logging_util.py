"""Tests for structured JSON logging utility."""

from __future__ import annotations

import json
import logging

import pytest

from src.logging_util import PhaseLogEntry, StructuredLogger


def test_log_phase_creates_entry() -> None:
    logger = StructuredLogger("test_create")
    logger.log_phase("DISCOVER", "ok", "Found 3 bugs")

    entries = logger.get_entries()
    assert len(entries) == 1
    assert entries[0].phase == "DISCOVER"
    assert entries[0].status == "ok"
    assert entries[0].summary == "Found 3 bugs"


def test_log_phase_emits_json_to_logger(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="test_json"):
        logger = StructuredLogger("test_json")
        logger.log_phase("FILTER", "ok", "Filtered bugs", bug_key="OCPBUGS-123")

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].message)
    assert payload["phase"] == "FILTER"
    assert payload["status"] == "ok"
    assert payload["summary"] == "Filtered bugs"
    assert payload["bug_key"] == "OCPBUGS-123"


def test_get_entries_filters_by_phase() -> None:
    logger = StructuredLogger("test_filter_phase")
    logger.log_phase("DISCOVER", "ok", "d1")
    logger.log_phase("FILTER", "ok", "f1")
    logger.log_phase("DISCOVER", "error", "d2")

    discover = logger.get_entries("DISCOVER")
    assert len(discover) == 2
    assert all(e.phase == "DISCOVER" for e in discover)

    filt = logger.get_entries("FILTER")
    assert len(filt) == 1
    assert filt[0].summary == "f1"


def test_get_entries_all_when_no_phase() -> None:
    logger = StructuredLogger("test_all")
    logger.log_phase("DISCOVER", "ok", "d1")
    logger.log_phase("MAP", "ok", "m1")

    entries = logger.get_entries()
    assert len(entries) == 2
    assert entries[0].phase == "DISCOVER"
    assert entries[1].phase == "MAP"


def test_count_by_status() -> None:
    logger = StructuredLogger("test_count")
    logger.log_phase("FILTER", "ok", "f1")
    logger.log_phase("FILTER", "ok", "f2")
    logger.log_phase("FILTER", "error", "f3")
    logger.log_phase("MAP", "ok", "m1")

    counts_filter = logger.count_by_status("FILTER")
    assert counts_filter == {"ok": 2, "error": 1}

    counts_all = logger.count_by_status()
    assert counts_all == {"ok": 3, "error": 1}


def test_total_tokens_sums_across_phases() -> None:
    logger = StructuredLogger("test_tokens")
    logger.log_phase("FILTER", "ok", "f1", tokens=100)
    logger.log_phase("MAP", "ok", "m1", tokens=250)
    logger.log_phase("ANALYZE", "ok", "a1", tokens=50)

    assert logger.total_tokens() == 400


def test_total_retries_sums_across_phases() -> None:
    logger = StructuredLogger("test_retries")
    logger.log_phase("FILTER", "ok", "f1", retries=1)
    logger.log_phase("MAP", "ok", "m1", retries=3)
    logger.log_phase("ANALYZE", "ok", "a1")

    assert logger.total_retries() == 4


def test_clear_removes_all_entries() -> None:
    logger = StructuredLogger("test_clear")
    logger.log_phase("DISCOVER", "ok", "d1")
    logger.log_phase("FILTER", "ok", "f1")
    assert len(logger.get_entries()) == 2

    logger.clear()
    assert len(logger.get_entries()) == 0


def test_log_phase_omits_falsy_values_from_json(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="test_falsy"):
        logger = StructuredLogger("test_falsy")
        logger.log_phase("DISCOVER", "ok", "d1")

    payload = json.loads(caplog.records[0].message)
    # Only truthy fields should be present
    assert "phase" in payload
    assert "status" in payload
    assert "summary" in payload
    # Falsy defaults should be omitted
    assert "bug_key" not in payload
    assert "model" not in payload
    assert "tokens" not in payload
    assert "elapsed_sec" not in payload
    assert "confidence" not in payload
    assert "retries" not in payload
    assert "cache_hit" not in payload
    assert "extra" not in payload


def test_phase_log_entry_is_frozen() -> None:
    entry = PhaseLogEntry(phase="DISCOVER", status="ok", summary="test")
    with pytest.raises(AttributeError):
        entry.phase = "FILTER"  # type: ignore[misc]
