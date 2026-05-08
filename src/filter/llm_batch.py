"""Anthropic Message Batch API support with prompt caching and retry.

Submits multiple LLM requests as a single Anthropic MessageBatch,
saving ~50% on token costs vs. sequential calls. Falls back to
sequential call_llm() for non-Anthropic providers.
"""

import logging
import time

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from src.filter.llm_config import LLMBackendConfig, LLMProvider
from src.filter.llm_filter import call_llm as _call_llm

logger = logging.getLogger(__name__)


def call_with_retry(
    fn,
    max_retries: int = 2,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
) -> tuple:
    """Call *fn* with exponential backoff on failure.

    Args:
        fn: Zero-argument callable to invoke.
        max_retries: Maximum number of retries after the initial attempt.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound on the delay between retries.

    Returns:
        (result, retries_used) where *retries_used* is 0 when the first
        attempt succeeds.

    Raises:
        The last exception raised by *fn* if all attempts fail.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = fn()
            return result, attempt
        except Exception as exc:
            last_error = exc
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    "Attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)

    # All attempts exhausted — re-raise the last error
    raise last_error  # type: ignore[misc]


def _build_batch_request(
    index: int,
    messages: list[dict],
    system_text: str,
    model: str,
) -> dict:
    """Build a single batch-request entry with prompt caching on the system message."""
    return {
        "custom_id": f"req_{index}",
        "params": {
            "model": model,
            "max_tokens": 1024,
            "system": [
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            "messages": messages,
        },
    }


def batch_call_llm(
    requests: list[dict],
    config: LLMBackendConfig,
    poll_interval: float = 10.0,
    timeout: float = 1800.0,
) -> list[str]:
    """Submit multiple LLM requests as an Anthropic MessageBatch.

    Args:
        requests: List of dicts, each with ``"messages"`` (list of message
                  dicts) and optionally ``"system"`` (system prompt text).
        config: LLM backend config.  Must be anthropic provider.
        poll_interval: Seconds between status polls.  Default 10 s.
        timeout: Max seconds to wait for batch completion.  Default 30 min.

    Returns:
        List of response texts in the same order as inputs.

    Raises:
        ValueError: If provider is not anthropic (batch only works with
                    Anthropic API).
        TimeoutError: If batch doesn't complete within *timeout*.
    """
    if config.provider != LLMProvider.ANTHROPIC:
        raise ValueError("Batch API only available with Anthropic provider")

    client = anthropic.Anthropic(api_key=config.api_key)

    # --- Build batch request entries ---
    batch_requests = []
    for i, req in enumerate(requests):
        user_messages = [m for m in req["messages"] if m["role"] != "system"]
        system_text = req.get("system", "")
        if not system_text:
            # Extract system prompt from messages if not provided separately
            for m in req["messages"]:
                if m["role"] == "system":
                    system_text = m["content"]
                    break
        batch_requests.append(
            _build_batch_request(i, user_messages, system_text, config.model),
        )

    logger.info("Creating Anthropic batch with %d requests", len(batch_requests))
    batch = client.messages.batches.create(requests=batch_requests)
    batch_id = batch.id
    logger.info("Batch created: %s", batch_id)

    # --- Poll until completion ---
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            raise TimeoutError(
                f"Batch {batch_id} did not complete within {timeout}s"
            )

        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            logger.info("Batch %s completed in %.1fs", batch_id, elapsed)
            break

        logger.debug(
            "Batch %s status: %s (%.0fs elapsed)",
            batch_id,
            batch.processing_status,
            elapsed,
        )
        time.sleep(poll_interval)

    # --- Collect results, keyed by custom_id for correct ordering ---
    results_by_id: dict[str, str] = {}
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        if result.result.type == "succeeded":
            text = result.result.message.content[0].text.strip()
        else:
            text = ""
            logger.warning(
                "Batch result %s was not successful: %s",
                custom_id,
                result.result.type,
            )
        results_by_id[custom_id] = text

    # Return in the original request order
    return [results_by_id.get(f"req_{i}", "") for i in range(len(requests))]


def batch_or_sequential(
    requests: list[dict],
    config: LLMBackendConfig,
    use_batch: bool = False,
) -> list[str]:
    """Use batch if enabled and provider supports it, else sequential.

    Args:
        requests: List of dicts, each with ``"messages"`` and optionally
                  ``"system"``.
        config: LLM backend config.
        use_batch: If True and provider is Anthropic, use
                   :func:`batch_call_llm`.  Otherwise fall back to
                   sequential :func:`_call_llm` calls.

    Returns:
        List of response texts in the same order as inputs.
    """
    if use_batch and config.provider == LLMProvider.ANTHROPIC:
        logger.info("Using Anthropic Batch API for %d requests", len(requests))
        return batch_call_llm(requests, config)

    logger.info("Using sequential LLM calls for %d requests", len(requests))
    results: list[str] = []
    for req in requests:
        messages = req["messages"]
        system_text = req.get("system")
        if system_text:
            # Prepend system message for _call_llm compatibility
            messages = [{"role": "system", "content": system_text}, *messages]
        results.append(_call_llm(messages, config))
    return results
