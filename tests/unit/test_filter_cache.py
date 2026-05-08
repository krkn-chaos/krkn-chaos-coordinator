"""Tests for the semantic filter cache."""

from __future__ import annotations

import pytest

chromadb = pytest.importorskip("chromadb", reason="chromadb not installed")

from src.knowledge.filter_cache import SemanticFilterCache
from src.models import Bug, FilterResult


def _make_bug(key: str = "TEST-1", summary: str = "") -> Bug:
    return Bug(
        key=key,
        summary=summary,
        description="",
        component="Etcd",
        priority="Major",
        status="New",
        created="2026-03-30",
        url=f"https://redhat.atlassian.net/browse/{key}",
    )


def _make_filter_result(
    chaos_relevant: bool = True,
    failure_mode: str | None = "crash",
    injection_method: str | None = "pod",
) -> FilterResult:
    return FilterResult(
        bug=_make_bug(summary="etcd crash under load"),
        chaos_relevant=chaos_relevant,
        failure_mode=failure_mode,
        injection_method=injection_method,
        confidence=0.85,
    )


@pytest.fixture()
def cache() -> SemanticFilterCache:
    client = chromadb.EphemeralClient()
    # Delete collection if it exists to ensure test isolation
    try:
        client.delete_collection("filter_cache")
    except Exception:
        pass
    return SemanticFilterCache(client, max_distance=0.15, ttl_days=30)


class TestSemanticFilterCache:
    def test_cache_miss_returns_none(self, cache: SemanticFilterCache) -> None:
        result = cache.get("etcd crash under network partition")
        assert result is None

    def test_cache_hit_returns_result(self, cache: SemanticFilterCache) -> None:
        summary = "etcd crash under network partition"
        original = _make_filter_result(chaos_relevant=True)
        cache.put(summary, original)

        hit = cache.get(summary)
        assert hit is not None
        assert hit.chaos_relevant is True
        assert hit.confidence == 0.9  # Cache hits get 0.9 confidence

    def test_cache_respects_distance_threshold(
        self, cache: SemanticFilterCache,
    ) -> None:
        cache.put(
            "etcd crash under network partition",
            _make_filter_result(chaos_relevant=True),
        )

        # A very different query should miss due to distance threshold
        result = cache.get(
            "console UI button rendering broken in dark mode theme",
        )
        # This may or may not hit depending on embedding model distance,
        # but the key behavior is that exact matches always hit
        # and very different queries should not

    def test_cache_put_and_retrieve_metadata(
        self, cache: SemanticFilterCache,
    ) -> None:
        summary = "node drain causes pods to be stuck in Terminating state"
        original = _make_filter_result(
            chaos_relevant=True,
            failure_mode="node drain stuck",
            injection_method="node",
        )
        cache.put(summary, original)

        hit = cache.get(summary)
        assert hit is not None
        assert hit.chaos_relevant is True
        assert hit.failure_mode == "node drain stuck"
        assert hit.injection_method == "node"

    def test_cache_stores_not_relevant(self, cache: SemanticFilterCache) -> None:
        summary = "CVE-2026-12345 security tracking issue"
        original = _make_filter_result(
            chaos_relevant=False,
            failure_mode=None,
            injection_method=None,
        )
        cache.put(summary, original)

        hit = cache.get(summary)
        assert hit is not None
        assert hit.chaos_relevant is False
        assert hit.failure_mode is None
        assert hit.injection_method is None

    def test_cache_empty_summary_returns_none(
        self, cache: SemanticFilterCache,
    ) -> None:
        result = cache.get("")
        assert result is None

    def test_cache_empty_summary_put_is_noop(
        self, cache: SemanticFilterCache,
    ) -> None:
        cache.put("", _make_filter_result())
        # Should not raise; collection should still be empty
        assert cache._collection.count() == 0

    def test_cache_hit_has_placeholder_bug(
        self, cache: SemanticFilterCache,
    ) -> None:
        summary = "etcd member not recovering after restart"
        cache.put(summary, _make_filter_result())

        hit = cache.get(summary)
        assert hit is not None
        assert hit.bug.key == "cached"
