"""Semantic cache for FILTER results using ChromaDB embeddings.

Cache-Aside pattern: check cache before calling LLM, store results after.
Bugs with similar summaries reuse previously-classified results, avoiding
redundant LLM calls.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import chromadb

from src.models import Bug, FilterResult

logger = logging.getLogger(__name__)


class SemanticFilterCache:
    """Cache-Aside pattern for FILTER results using ChromaDB embeddings."""

    def __init__(
        self,
        chroma_client: chromadb.ClientAPI,
        max_distance: float = 0.15,
        ttl_days: int = 30,
    ):
        self._collection = chroma_client.get_or_create_collection(
            name="filter_cache",
            metadata={"hnsw:space": "cosine"},
        )
        self._max_distance = max_distance
        self._ttl_days = ttl_days

    def get(self, bug_summary: str) -> FilterResult | None:
        """Check cache. Returns FilterResult on hit, None on miss."""
        if not bug_summary or not bug_summary.strip():
            return None

        try:
            count = self._collection.count()
            if count == 0:
                return None

            results = self._collection.query(
                query_texts=[bug_summary],
                n_results=1,
            )
        except Exception as e:
            logger.warning("Filter cache query failed: %s", e)
            return None

        if not results or not results.get("distances") or not results["distances"][0]:
            return None

        distance = results["distances"][0][0]
        if distance > self._max_distance:
            return None

        metadata = results["metadatas"][0][0]

        # Check TTL expiration
        cached_at = metadata.get("cached_at", "")
        if cached_at and self._is_expired(cached_at):
            logger.debug("Filter cache hit expired (cached_at=%s)", cached_at)
            return None

        # Build FilterResult from cached metadata
        placeholder_bug = Bug(
            key="cached",
            summary=bug_summary,
            description="",
            component="",
            priority="",
            status="",
            created="",
            url="",
        )

        return FilterResult(
            bug=placeholder_bug,
            chaos_relevant=metadata.get("chaos_relevant", "false") == "true",
            failure_mode=metadata.get("failure_mode") or None,
            injection_method=metadata.get("injection_method") or None,
            confidence=0.9,
        )

    def put(self, bug_summary: str, result: FilterResult) -> None:
        """Store classification in cache after LLM call."""
        if not bug_summary or not bug_summary.strip():
            return

        doc_id = hashlib.sha256(bug_summary.encode("utf-8")).hexdigest()[:32]

        metadata = {
            "chaos_relevant": "true" if result.chaos_relevant else "false",
            "failure_mode": result.failure_mode or "",
            "injection_method": result.injection_method or "",
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            self._collection.upsert(
                ids=[doc_id],
                documents=[bug_summary],
                metadatas=[metadata],
            )
        except Exception as e:
            logger.warning("Filter cache put failed: %s", e)

    def _is_expired(self, cached_at_iso: str) -> bool:
        """Check if a cached entry has exceeded the TTL."""
        try:
            cached_time = datetime.fromisoformat(cached_at_iso)
            now = datetime.now(timezone.utc)
            age_days = (now - cached_time).days
            return age_days > self._ttl_days
        except (ValueError, TypeError):
            # If we can't parse the timestamp, treat as expired
            return True
