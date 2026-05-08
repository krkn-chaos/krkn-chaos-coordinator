"""Tests for the Anthropic Batch API module."""

from unittest.mock import MagicMock, patch

import pytest

from src.filter.llm_batch import (
    batch_call_llm,
    batch_or_sequential,
    call_with_retry,
)
from src.filter.llm_config import LLMBackendConfig, LLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _anthropic_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.ANTHROPIC,
        model="claude-sonnet-4-6",
        api_key="sk-ant-test-key",
    )


def _ollama_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.OLLAMA,
        model="qwen2.5-coder:14b",
    )


def _sample_requests() -> list[dict]:
    return [
        {
            "system": "You are a chaos expert.",
            "messages": [{"role": "user", "content": "Analyze bug A"}],
        },
        {
            "system": "You are a chaos expert.",
            "messages": [{"role": "user", "content": "Analyze bug B"}],
        },
        {
            "system": "You are a chaos expert.",
            "messages": [{"role": "user", "content": "Analyze bug C"}],
        },
    ]


def _mock_batch_result(custom_id: str, text: str) -> MagicMock:
    """Build a mock batch result entry."""
    content_block = MagicMock()
    content_block.text = text

    message = MagicMock()
    message.content = [content_block]

    result = MagicMock()
    result.type = "succeeded"
    result.message = message

    entry = MagicMock()
    entry.custom_id = custom_id
    entry.result = result
    return entry


# ---------------------------------------------------------------------------
# Tests: batch_call_llm
# ---------------------------------------------------------------------------

class TestBatchOnlyWorksWithAnthropic:
    def test_non_anthropic_raises_value_error(self):
        with pytest.raises(ValueError, match="Batch API only available with Anthropic provider"):
            batch_call_llm(
                requests=_sample_requests(),
                config=_ollama_config(),
            )

    def test_none_provider_raises_value_error(self):
        config = LLMBackendConfig(provider=LLMProvider.NONE, model="")
        with pytest.raises(ValueError, match="Batch API only available with Anthropic provider"):
            batch_call_llm(requests=_sample_requests(), config=config)


class TestBatchCreatesRequestsWithCacheControl:
    @patch("src.filter.llm_batch.anthropic")
    def test_request_format_has_cache_control(self, mock_anthropic_module):
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        # Batch creation returns an object with id
        mock_batch = MagicMock()
        mock_batch.id = "batch_test_123"
        mock_batch.processing_status = "ended"
        mock_client.messages.batches.create.return_value = mock_batch

        # Retrieve immediately returns ended
        mock_client.messages.batches.retrieve.return_value = mock_batch

        # Results return in order
        mock_client.messages.batches.results.return_value = [
            _mock_batch_result("req_0", "response_0"),
        ]

        requests = [
            {
                "system": "You are a chaos expert.",
                "messages": [{"role": "user", "content": "Analyze bug A"}],
            },
        ]
        batch_call_llm(requests=requests, config=_anthropic_config())

        # Verify the batch create call
        call_args = mock_client.messages.batches.create.call_args
        batch_requests = call_args.kwargs["requests"]
        assert len(batch_requests) == 1

        req = batch_requests[0]
        assert req["custom_id"] == "req_0"
        assert req["params"]["model"] == "claude-sonnet-4-6"
        assert req["params"]["max_tokens"] == 1024

        # Verify cache_control on system message
        system_blocks = req["params"]["system"]
        assert len(system_blocks) == 1
        assert system_blocks[0]["type"] == "text"
        assert system_blocks[0]["text"] == "You are a chaos expert."
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

        # Messages should only contain user messages (no system)
        assert req["params"]["messages"] == [
            {"role": "user", "content": "Analyze bug A"},
        ]


class TestBatchReturnsResultsInOrder:
    @patch("src.filter.llm_batch.anthropic")
    def test_results_match_input_order(self, mock_anthropic_module):
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_batch = MagicMock()
        mock_batch.id = "batch_order_test"
        mock_batch.processing_status = "ended"
        mock_client.messages.batches.create.return_value = mock_batch
        mock_client.messages.batches.retrieve.return_value = mock_batch

        # Return results in REVERSE order to test ordering logic
        mock_client.messages.batches.results.return_value = [
            _mock_batch_result("req_2", "response_C"),
            _mock_batch_result("req_0", "response_A"),
            _mock_batch_result("req_1", "response_B"),
        ]

        results = batch_call_llm(
            requests=_sample_requests(),
            config=_anthropic_config(),
        )

        assert results == ["response_A", "response_B", "response_C"]

    @patch("src.filter.llm_batch.anthropic")
    def test_missing_result_returns_empty_string(self, mock_anthropic_module):
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_batch = MagicMock()
        mock_batch.id = "batch_missing"
        mock_batch.processing_status = "ended"
        mock_client.messages.batches.create.return_value = mock_batch
        mock_client.messages.batches.retrieve.return_value = mock_batch

        # Only return result for req_0, skip req_1
        mock_client.messages.batches.results.return_value = [
            _mock_batch_result("req_0", "response_A"),
        ]

        requests = _sample_requests()[:2]
        results = batch_call_llm(requests=requests, config=_anthropic_config())

        assert results[0] == "response_A"
        assert results[1] == ""


class TestBatchTimeoutRaisesError:
    @patch("src.filter.llm_batch.time")
    @patch("src.filter.llm_batch.anthropic")
    def test_timeout_raises_timeout_error(self, mock_anthropic_module, mock_time):
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        mock_batch = MagicMock()
        mock_batch.id = "batch_timeout_test"
        mock_batch.processing_status = "in_progress"
        mock_client.messages.batches.create.return_value = mock_batch
        mock_client.messages.batches.retrieve.return_value = mock_batch

        # Simulate time passing beyond timeout
        # First call for start, then each loop iteration
        mock_time.monotonic.side_effect = [0.0, 100.0]
        mock_time.sleep = MagicMock()

        with pytest.raises(TimeoutError, match="batch_timeout_test"):
            batch_call_llm(
                requests=_sample_requests(),
                config=_anthropic_config(),
                timeout=50.0,
            )


# ---------------------------------------------------------------------------
# Tests: batch_or_sequential
# ---------------------------------------------------------------------------

class TestBatchOrSequentialUsesBatchWhenEnabled:
    @patch("src.filter.llm_batch.batch_call_llm")
    def test_anthropic_with_batch_enabled(self, mock_batch):
        mock_batch.return_value = ["r1", "r2", "r3"]
        requests = _sample_requests()

        results = batch_or_sequential(
            requests=requests,
            config=_anthropic_config(),
            use_batch=True,
        )

        mock_batch.assert_called_once_with(requests, _anthropic_config())
        assert results == ["r1", "r2", "r3"]


class TestBatchOrSequentialFallsBackToSequential:
    @patch("src.filter.llm_batch._call_llm")
    def test_batch_disabled_uses_sequential(self, mock_call_llm):
        mock_call_llm.side_effect = ["r1", "r2", "r3"]
        requests = _sample_requests()

        results = batch_or_sequential(
            requests=requests,
            config=_anthropic_config(),
            use_batch=False,
        )

        assert mock_call_llm.call_count == 3
        assert results == ["r1", "r2", "r3"]

    @patch("src.filter.llm_batch._call_llm")
    def test_non_anthropic_uses_sequential_even_with_batch_enabled(self, mock_call_llm):
        mock_call_llm.side_effect = ["r1", "r2"]
        requests = _sample_requests()[:2]

        results = batch_or_sequential(
            requests=requests,
            config=_ollama_config(),
            use_batch=True,
        )

        assert mock_call_llm.call_count == 2
        assert results == ["r1", "r2"]


# ---------------------------------------------------------------------------
# Tests: call_with_retry
# ---------------------------------------------------------------------------

class TestCallWithRetry:
    def test_succeeds_on_first_attempt(self):
        fn = MagicMock(return_value="ok")
        result, retries = call_with_retry(fn)
        assert result == "ok"
        assert retries == 0
        fn.assert_called_once()

    def test_retries_on_failure_then_succeeds(self):
        fn = MagicMock(side_effect=[RuntimeError("fail"), "ok"])
        result, retries = call_with_retry(fn, max_retries=2, base_delay=0.01)
        assert result == "ok"
        assert retries == 1
        assert fn.call_count == 2

    def test_exhausts_retries_and_raises(self):
        error = RuntimeError("persistent failure")
        fn = MagicMock(side_effect=error)
        with pytest.raises(RuntimeError, match="persistent failure"):
            call_with_retry(fn, max_retries=2, base_delay=0.01)
        assert fn.call_count == 3  # initial + 2 retries

    def test_delay_is_capped_by_max_delay(self):
        fn = MagicMock(side_effect=[RuntimeError("fail"), RuntimeError("fail"), "ok"])
        with patch("src.filter.llm_batch.time.sleep") as mock_sleep:
            result, retries = call_with_retry(
                fn, max_retries=3, base_delay=5.0, max_delay=8.0,
            )
        assert result == "ok"
        assert retries == 2
        # First retry: min(5.0 * 2^0, 8.0) = 5.0
        # Second retry: min(5.0 * 2^1, 8.0) = 8.0
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [5.0, 8.0]

    def test_zero_retries_means_single_attempt(self):
        fn = MagicMock(side_effect=RuntimeError("fail"))
        with pytest.raises(RuntimeError, match="fail"):
            call_with_retry(fn, max_retries=0, base_delay=0.01)
        fn.assert_called_once()
