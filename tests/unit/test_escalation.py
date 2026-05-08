"""Tests for confidence-based FILTER escalation to Opus."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.filter.llm_config import LLMBackendConfig, LLMProvider
from src.filter.llm_filter import llm_filter_bug
from src.models import Bug


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_bug(**overrides: object) -> Bug:
    defaults = {
        "key": "OCPBUGS-12345",
        "summary": "etcd operator reports Degraded after node reboot",
        "description": "After rebooting a master node, the etcd operator Degraded condition is not cleared.",
        "component": "etcd",
        "priority": "Critical",
        "status": "New",
        "created": "2026-01-15",
        "url": "https://issues.redhat.com/browse/OCPBUGS-12345",
    }
    return Bug(**{**defaults, **overrides})


def _sonnet_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.ANTHROPIC,
        model="claude-sonnet-4-6",
        api_key="sk-ant-test",
    )


def _opus_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.ANTHROPIC,
        model="claude-opus-4-6",
        api_key="sk-ant-test",
    )


def _llm_response(chaos_relevant: bool = True, confidence: int = 90) -> str:
    return json.dumps({
        "chaos_relevant": chaos_relevant,
        "confidence": confidence,
        "failure_mode": "etcd degraded after node reboot",
        "injection_method": "node_scenarios",
        "skip_reason": None,
    })


# ---------------------------------------------------------------------------
# Tests: high confidence (no escalation)
# ---------------------------------------------------------------------------


class TestHighConfidenceNoEscalation:

    @patch("src.filter.llm_filter.call_llm")
    @patch("src.filter.llm_filter.detect_llm_backend")
    def test_high_confidence_no_escalation(
        self,
        mock_detect: MagicMock,
        mock_call_llm: MagicMock,
    ) -> None:
        """confidence=90 should NOT trigger a second LLM call."""
        mock_detect.return_value = _sonnet_config()
        mock_call_llm.return_value = _llm_response(confidence=90)

        bug = _make_bug()
        result = llm_filter_bug(bug)

        assert result.chaos_relevant is True
        assert result.failure_mode == "etcd degraded after node reboot"
        # call_llm should be called exactly once (no escalation)
        assert mock_call_llm.call_count == 1

    @patch("src.filter.llm_filter.call_llm")
    @patch("src.filter.llm_filter.detect_llm_backend")
    def test_confidence_exactly_80_no_escalation(
        self,
        mock_detect: MagicMock,
        mock_call_llm: MagicMock,
    ) -> None:
        """confidence=80 (boundary) should NOT trigger escalation."""
        mock_detect.return_value = _sonnet_config()
        mock_call_llm.return_value = _llm_response(confidence=80)

        result = llm_filter_bug(_make_bug())

        assert mock_call_llm.call_count == 1


# ---------------------------------------------------------------------------
# Tests: low confidence triggers escalation
# ---------------------------------------------------------------------------


class TestLowConfidenceEscalation:

    @patch("src.filter.llm_filter.detect_llm_backend")
    @patch("src.filter.llm_filter.call_llm")
    def test_low_confidence_triggers_escalation(
        self,
        mock_call_llm: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """confidence=55 should trigger a second call with Opus config."""
        sonnet_cfg = _sonnet_config()
        opus_cfg = _opus_config()

        # First call returns filter config, second returns analyze (Opus) config
        mock_detect.side_effect = [sonnet_cfg, opus_cfg]

        # First response: low confidence; second response: Opus result
        opus_response = json.dumps({
            "chaos_relevant": False,
            "confidence": 95,
            "failure_mode": None,
            "injection_method": None,
            "skip_reason": "Code logic bug, not chaos-relevant",
        })
        mock_call_llm.side_effect = [
            _llm_response(confidence=55),
            opus_response,
        ]

        bug = _make_bug()
        result = llm_filter_bug(bug)

        # Two calls: initial + escalation
        assert mock_call_llm.call_count == 2
        # Result should reflect the Opus (escalated) response
        assert result.chaos_relevant is False
        assert result.skip_reason == "Code logic bug, not chaos-relevant"

    @patch("src.filter.llm_filter.detect_llm_backend")
    @patch("src.filter.llm_filter.call_llm")
    def test_escalation_passes_system_prompt(
        self,
        mock_call_llm: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """Escalated call should also pass system_prompt for caching."""
        mock_detect.side_effect = [_sonnet_config(), _opus_config()]
        mock_call_llm.side_effect = [
            _llm_response(confidence=30),
            _llm_response(confidence=95),
        ]

        llm_filter_bug(_make_bug())

        # Both calls should have system_prompt kwarg
        for call in mock_call_llm.call_args_list:
            assert "system_prompt" in call.kwargs
            assert call.kwargs["system_prompt"] is not None


# ---------------------------------------------------------------------------
# Tests: Opus never escalates
# ---------------------------------------------------------------------------


class TestOpusNeverEscalates:

    @patch("src.filter.llm_filter.call_llm")
    @patch("src.filter.llm_filter.detect_llm_backend")
    def test_opus_never_escalates(
        self,
        mock_detect: MagicMock,
        mock_call_llm: MagicMock,
    ) -> None:
        """If already on Opus, no escalation regardless of confidence."""
        mock_detect.return_value = _opus_config()
        mock_call_llm.return_value = _llm_response(confidence=20)

        result = llm_filter_bug(_make_bug())

        # Only one call, even though confidence is very low
        assert mock_call_llm.call_count == 1
        assert result.chaos_relevant is True

    @patch("src.filter.llm_filter.call_llm")
    @patch("src.filter.llm_filter.detect_llm_backend")
    def test_opus_variant_model_no_escalation(
        self,
        mock_detect: MagicMock,
        mock_call_llm: MagicMock,
    ) -> None:
        """Model names containing 'opus' (any case) should not escalate."""
        config = LLMBackendConfig(
            provider=LLMProvider.ANTHROPIC,
            model="claude-opus-4-5-20250630",
            api_key="sk-ant-test",
        )
        mock_detect.return_value = config
        mock_call_llm.return_value = _llm_response(confidence=10)

        llm_filter_bug(_make_bug())

        assert mock_call_llm.call_count == 1


# ---------------------------------------------------------------------------
# Tests: escalation uses correct config
# ---------------------------------------------------------------------------


class TestEscalationConfig:

    @patch("src.filter.llm_filter.detect_llm_backend")
    @patch("src.filter.llm_filter.call_llm")
    def test_escalation_uses_analyze_phase_config(
        self,
        mock_call_llm: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """Escalated call should use detect_llm_backend(phase='analyze')."""
        sonnet_cfg = _sonnet_config()
        opus_cfg = _opus_config()

        mock_detect.side_effect = [sonnet_cfg, opus_cfg]
        mock_call_llm.side_effect = [
            _llm_response(confidence=50),
            _llm_response(confidence=95),
        ]

        llm_filter_bug(_make_bug())

        # detect_llm_backend should be called twice:
        # 1) phase="filter" for initial config
        # 2) phase="analyze" for escalation
        assert mock_detect.call_count == 2
        assert mock_detect.call_args_list[0].kwargs.get("phase") == "filter"
        assert mock_detect.call_args_list[1].kwargs.get("phase") == "analyze"

        # Second call_llm should use opus config
        second_call = mock_call_llm.call_args_list[1]
        assert second_call.args[1] == opus_cfg

    @patch("src.filter.llm_filter.detect_llm_backend")
    @patch("src.filter.llm_filter.call_llm")
    def test_filter_uses_filter_phase_by_default(
        self,
        mock_call_llm: MagicMock,
        mock_detect: MagicMock,
    ) -> None:
        """llm_filter_bug with config=None should call detect_llm_backend(phase='filter')."""
        mock_detect.return_value = _sonnet_config()
        mock_call_llm.return_value = _llm_response(confidence=90)

        llm_filter_bug(_make_bug())

        mock_detect.assert_called_once_with(phase="filter")


# ---------------------------------------------------------------------------
# Tests: confidence normalization
# ---------------------------------------------------------------------------


class TestConfidenceNormalization:

    @patch("src.filter.llm_filter.call_llm")
    @patch("src.filter.llm_filter.detect_llm_backend")
    def test_fractional_confidence_normalized(
        self,
        mock_detect: MagicMock,
        mock_call_llm: MagicMock,
    ) -> None:
        """confidence=0.5 (0-1 scale) should be treated as 50 and trigger escalation."""
        mock_detect.side_effect = [_sonnet_config(), _opus_config()]

        response_low = json.dumps({
            "chaos_relevant": True,
            "confidence": 0.5,
            "failure_mode": "test",
            "injection_method": "pod kill",
            "skip_reason": None,
        })
        mock_call_llm.side_effect = [response_low, _llm_response(confidence=95)]

        llm_filter_bug(_make_bug())

        # 0.5 -> 50 < 80 -> should escalate
        assert mock_call_llm.call_count == 2

    @patch("src.filter.llm_filter.call_llm")
    @patch("src.filter.llm_filter.detect_llm_backend")
    def test_missing_confidence_defaults_to_100(
        self,
        mock_detect: MagicMock,
        mock_call_llm: MagicMock,
    ) -> None:
        """Missing confidence field should default to 100 (no escalation)."""
        mock_detect.return_value = _sonnet_config()

        response_no_confidence = json.dumps({
            "chaos_relevant": True,
            "failure_mode": "test",
            "injection_method": "pod kill",
            "skip_reason": None,
        })
        mock_call_llm.return_value = response_no_confidence

        llm_filter_bug(_make_bug())

        # Default 1.0 -> 100 >= 80 -> no escalation
        assert mock_call_llm.call_count == 1
