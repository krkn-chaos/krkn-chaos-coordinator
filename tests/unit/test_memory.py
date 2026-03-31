"""Tests for the memory store."""

from pathlib import Path

from src.knowledge.memory import MemoryStore
from src.models import AgentResult, Bug, FilterResult, GapAnalysis, Confidence, ActionType


def _make_bug(key="TEST-1"):
    return Bug(key=key, summary="test", description="desc", component="Etcd",
               priority="Major", status="New", created="2026-03-31", url="")


class TestMemoryStore:
    def test_new_store_is_empty(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert not store.is_bug_analyzed("TEST-1")
        assert store.get_analyzed_bug_keys() == set()
        stats = store.get_stats()
        assert stats["total_bugs_analyzed"] == 0

    def test_remember_result(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        result = AgentResult(
            agent_name="control_plane",
            bugs_discovered=[_make_bug("BUG-1"), _make_bug("BUG-2")],
        )
        summary = store.remember_result(result)
        assert summary["new_bugs"] == 2
        assert store.is_bug_analyzed("BUG-1")
        assert store.is_bug_analyzed("BUG-2")

    def test_skip_already_analyzed(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        result = AgentResult(agent_name="test", bugs_discovered=[_make_bug("BUG-1")])
        store.remember_result(result)

        # Second run with same bug
        result2 = AgentResult(agent_name="test", bugs_discovered=[_make_bug("BUG-1")])
        summary = store.remember_result(result2)
        assert summary["new_bugs"] == 0
        assert summary["skipped_known"] == 1

    def test_persists_across_loads(self, tmp_path):
        path = tmp_path / "memory.json"
        store1 = MemoryStore(path)
        store1.remember_result(AgentResult(agent_name="test", bugs_discovered=[_make_bug("BUG-1")]))

        store2 = MemoryStore(path)
        assert store2.is_bug_analyzed("BUG-1")

    def test_remember_gaps(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        gap = GapAnalysis(bug=_make_bug("BUG-1"), confidence_score=75,
                          confidence_level=Confidence.HIGH, action_type=ActionType.DRAFT_PR)
        result = AgentResult(agent_name="test", bugs_discovered=[_make_bug("BUG-1")], gaps=[gap])
        store.remember_result(result)
        assert len(store.get_open_gaps()) == 1

    def test_mark_gap_resolved(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        gap = GapAnalysis(bug=_make_bug("BUG-1"), confidence_score=75,
                          confidence_level=Confidence.HIGH, action_type=ActionType.DRAFT_PR)
        result = AgentResult(agent_name="test", bugs_discovered=[_make_bug("BUG-1")], gaps=[gap])
        store.remember_result(result)

        store.mark_gap_resolved("BUG-1", "https://github.com/krkn-chaos/krkn/issues/99")
        assert len(store.get_open_gaps()) == 0

    def test_add_finding(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.add_finding("control_plane", "etcd health check timeout is too short under load")
        stats = store.get_stats()
        assert stats["total_findings"] == 1

    def test_run_history(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        result = AgentResult(agent_name="test", bugs_discovered=[_make_bug()])
        store.remember_result(result)
        history = store.get_run_history()
        assert len(history) == 1
        assert history[0]["agent"] == "test"
