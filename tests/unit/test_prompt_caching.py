"""Tests for Anthropic prompt caching in call_llm()."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.filter.llm_config import LLMBackendConfig, LLMProvider
from src.filter.llm_filter import _prepend_system_message


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SYSTEM_TEXT = "You are a chaos engineering expert."
USER_TEXT = "Analyze this bug."


def _anthropic_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.ANTHROPIC,
        model="claude-sonnet-4-6",
        api_key="sk-ant-test",
    )


def _ollama_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.OLLAMA,
        model="qwen2.5-coder:14b",
        base_url="http://localhost:11434",
    )


def _openai_config() -> LLMBackendConfig:
    return LLMBackendConfig(
        provider=LLMProvider.OPENAI,
        model="gpt-4o",
        api_key="sk-openai-test",
    )


def _call_llm_with_mock_anthropic(
    messages: list[dict],
    config: LLMBackendConfig,
    system_prompt: str | None = None,
) -> tuple[MagicMock, str]:
    """Call call_llm() with a mocked Anthropic client, return (mock_client, result)."""
    mock_anthropic = MagicMock()
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"result": "ok"}')]
    mock_client.messages.create.return_value = mock_response

    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        from src.filter.llm_filter import call_llm
        result = call_llm(messages, config, system_prompt=system_prompt)

    return mock_client, result


def _call_llm_with_mock_ollama(
    messages: list[dict],
    config: LLMBackendConfig,
    system_prompt: str | None = None,
) -> tuple[MagicMock, str]:
    """Call call_llm() with a mocked Ollama module, return (mock_ollama, result)."""
    mock_ollama = MagicMock()
    mock_ollama.chat.return_value = {"message": {"content": '{"result": "ok"}'}}

    with patch.dict(sys.modules, {"ollama": mock_ollama}):
        from src.filter.llm_filter import call_llm
        result = call_llm(messages, config, system_prompt=system_prompt)

    return mock_ollama, result


def _call_llm_with_mock_openai(
    messages: list[dict],
    config: LLMBackendConfig,
    system_prompt: str | None = None,
) -> tuple[MagicMock, str]:
    """Call call_llm() with a mocked OpenAI module, return (mock_client, result)."""
    mock_openai = MagicMock()
    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content='{"ok": true}'))]
    mock_client.chat.completions.create.return_value = mock_response

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from src.filter.llm_filter import call_llm
        result = call_llm(messages, config, system_prompt=system_prompt)

    return mock_client, result


# ---------------------------------------------------------------------------
# Tests: Anthropic prompt caching
# ---------------------------------------------------------------------------


class TestAnthropicCacheControl:

    def test_anthropic_call_uses_cache_control(self) -> None:
        """When system_prompt is provided, Anthropic gets cache_control in system param."""
        messages = [{"role": "user", "content": USER_TEXT}]
        mock_client, _ = _call_llm_with_mock_anthropic(
            messages, _anthropic_config(), system_prompt=SYSTEM_TEXT,
        )

        create_kwargs = mock_client.messages.create.call_args
        system_param = create_kwargs.kwargs["system"]

        # Must be a list with cache_control
        assert isinstance(system_param, list)
        assert len(system_param) == 1
        assert system_param[0]["type"] == "text"
        assert system_param[0]["text"] == SYSTEM_TEXT
        assert system_param[0]["cache_control"] == {"type": "ephemeral"}

        # Messages should contain only user messages, no system
        passed_messages = create_kwargs.kwargs["messages"]
        assert all(m["role"] != "system" for m in passed_messages)
        assert passed_messages[0]["content"] == USER_TEXT

    def test_anthropic_without_system_prompt_uses_legacy(self) -> None:
        """When system_prompt is None, Anthropic extracts system from messages (legacy)."""
        messages = [
            {"role": "system", "content": SYSTEM_TEXT},
            {"role": "user", "content": USER_TEXT},
        ]
        mock_client, _ = _call_llm_with_mock_anthropic(messages, _anthropic_config())

        create_kwargs = mock_client.messages.create.call_args
        system_param = create_kwargs.kwargs["system"]

        # Legacy path: system is a plain string, no cache_control
        assert system_param == SYSTEM_TEXT
        assert not isinstance(system_param, list)

    def test_anthropic_max_tokens_is_1024(self) -> None:
        """Anthropic calls should use max_tokens=1024."""
        messages = [{"role": "user", "content": USER_TEXT}]
        mock_client, _ = _call_llm_with_mock_anthropic(
            messages, _anthropic_config(), system_prompt=SYSTEM_TEXT,
        )

        create_kwargs = mock_client.messages.create.call_args
        assert create_kwargs.kwargs["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# Tests: Non-Anthropic providers keep system in messages
# ---------------------------------------------------------------------------


class TestNonAnthropicSystemInMessages:

    def test_ollama_keeps_system_in_messages(self) -> None:
        """Ollama should receive system_prompt as a message, not via cache_control."""
        messages = [{"role": "user", "content": USER_TEXT}]
        mock_ollama, _ = _call_llm_with_mock_ollama(
            messages, _ollama_config(), system_prompt=SYSTEM_TEXT,
        )

        call_kwargs = mock_ollama.chat.call_args
        passed_messages = call_kwargs.kwargs["messages"]

        # System message should be prepended
        assert passed_messages[0]["role"] == "system"
        assert passed_messages[0]["content"] == SYSTEM_TEXT
        assert passed_messages[1]["role"] == "user"
        assert passed_messages[1]["content"] == USER_TEXT

    def test_openai_keeps_system_in_messages(self) -> None:
        """OpenAI should receive system_prompt as a message in the list."""
        messages = [{"role": "user", "content": USER_TEXT}]
        mock_client, _ = _call_llm_with_mock_openai(
            messages, _openai_config(), system_prompt=SYSTEM_TEXT,
        )

        call_kwargs = mock_client.chat.completions.create.call_args
        passed_messages = call_kwargs.kwargs["messages"]

        assert passed_messages[0]["role"] == "system"
        assert passed_messages[0]["content"] == SYSTEM_TEXT
        assert passed_messages[1]["role"] == "user"


# ---------------------------------------------------------------------------
# Tests: system_prompt takes precedence over messages
# ---------------------------------------------------------------------------


class TestSystemPromptPrecedence:

    def test_system_prompt_param_takes_precedence(self) -> None:
        """When system_prompt is passed, it should be used even if messages contain a system message."""
        old_system = "Old system prompt from messages"
        new_system = "New system prompt via parameter"

        messages = [
            {"role": "system", "content": old_system},
            {"role": "user", "content": USER_TEXT},
        ]
        mock_client, _ = _call_llm_with_mock_anthropic(
            messages, _anthropic_config(), system_prompt=new_system,
        )

        create_kwargs = mock_client.messages.create.call_args
        system_param = create_kwargs.kwargs["system"]

        # The parameter system_prompt should win
        assert isinstance(system_param, list)
        assert system_param[0]["text"] == new_system

        # The old system message should NOT appear in messages
        passed_messages = create_kwargs.kwargs["messages"]
        assert all(m["role"] != "system" for m in passed_messages)

    def test_system_prompt_replaces_existing_system_for_ollama(self) -> None:
        """For non-Anthropic, system_prompt replaces any existing system message."""
        messages = [
            {"role": "system", "content": "old system"},
            {"role": "user", "content": USER_TEXT},
        ]
        mock_ollama, _ = _call_llm_with_mock_ollama(
            messages, _ollama_config(), system_prompt="new system",
        )

        call_kwargs = mock_ollama.chat.call_args
        passed_messages = call_kwargs.kwargs["messages"]

        # Only one system message, and it should be the new one
        system_msgs = [m for m in passed_messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0]["content"] == "new system"


# ---------------------------------------------------------------------------
# Tests: _prepend_system_message helper
# ---------------------------------------------------------------------------


class TestPrependSystemMessage:

    def test_none_returns_original(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        result = _prepend_system_message(msgs, None)
        assert result is msgs

    def test_prepends_and_removes_existing(self) -> None:
        msgs = [
            {"role": "system", "content": "old"},
            {"role": "user", "content": "hello"},
        ]
        result = _prepend_system_message(msgs, "new")
        assert result[0] == {"role": "system", "content": "new"}
        assert result[1] == {"role": "user", "content": "hello"}
        assert len(result) == 2

    def test_does_not_mutate_original(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        result = _prepend_system_message(msgs, "system text")
        # Original should be untouched
        assert len(msgs) == 1
        assert len(result) == 2
