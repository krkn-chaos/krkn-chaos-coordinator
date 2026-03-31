"""Graphiti-based memory for the REMEMBER phase.

Stores analyzed bugs, gaps, actions, and findings in a temporal knowledge graph
so the system doesn't re-analyze the same bugs on subsequent runs.

Falls back to a JSON file store when Neo4j/Graphiti is not available.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from src.models import AgentResult, FilterResult, GapAnalysis

logger = logging.getLogger(__name__)

MEMORY_FILE = Path("./coordinator_memory.json")


class MemoryStore:
    """Persistent memory for tracking analyzed bugs across runs.

    Uses a JSON file as the default backend. Can be extended to use
    Graphiti + Neo4j when available.
    """

    def __init__(self, memory_path: Path = MEMORY_FILE):
        self._path = memory_path
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            with open(self._path) as f:
                return json.load(f)
        return {
            "analyzed_bugs": {},
            "gaps": {},
            "actions": {},
            "findings": [],
            "runs": [],
        }

    def _save(self) -> None:
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    def is_bug_analyzed(self, bug_key: str) -> bool:
        """Check if a bug has already been analyzed in a previous run."""
        return bug_key in self._data["analyzed_bugs"]

    def get_analyzed_bug_keys(self) -> set[str]:
        """Get all previously analyzed bug keys."""
        return set(self._data["analyzed_bugs"].keys())

    def remember_result(self, result: AgentResult) -> dict:
        """Store the results of an agent run.

        Returns summary of what was remembered.
        """
        timestamp = datetime.now().isoformat()
        new_bugs = 0
        new_gaps = 0
        skipped_known = 0

        # Remember analyzed bugs
        for bug in result.bugs_discovered:
            if bug.key not in self._data["analyzed_bugs"]:
                self._data["analyzed_bugs"][bug.key] = {
                    "summary": bug.summary,
                    "component": bug.component,
                    "analyzed_at": timestamp,
                    "agent": result.agent_name,
                }
                new_bugs += 1
            else:
                skipped_known += 1

        # Remember skipped bugs (with reason)
        for fr in result.bugs_filtered_out:
            if fr.bug.key not in self._data["analyzed_bugs"]:
                self._data["analyzed_bugs"][fr.bug.key] = {
                    "summary": fr.bug.summary,
                    "component": fr.bug.component,
                    "analyzed_at": timestamp,
                    "agent": result.agent_name,
                    "chaos_relevant": False,
                    "skip_reason": fr.skip_reason,
                }

        # Remember gaps
        for gap in result.gaps:
            gap_key = f"{gap.bug.key}_{result.agent_name}"
            if gap_key not in self._data["gaps"]:
                self._data["gaps"][gap_key] = {
                    "bug_key": gap.bug.key,
                    "bug_summary": gap.bug.summary,
                    "component": gap.bug.component,
                    "confidence": gap.confidence_score,
                    "action_type": gap.action_type.value,
                    "base_scenario": gap.base_scenario,
                    "reasoning": gap.reasoning,
                    "created_at": timestamp,
                    "agent": result.agent_name,
                    "status": "open",
                }
                new_gaps += 1

        # Remember the run
        self._data["runs"].append({
            "timestamp": timestamp,
            "agent": result.agent_name,
            "bugs_discovered": len(result.bugs_discovered),
            "bugs_filtered_out": len(result.bugs_filtered_out),
            "bugs_matched": len(result.bugs_matched),
            "gaps_found": len(result.gaps),
            "new_bugs": new_bugs,
            "skipped_known": skipped_known,
        })

        self._save()

        summary = {
            "new_bugs": new_bugs,
            "new_gaps": new_gaps,
            "skipped_known": skipped_known,
        }
        logger.info(
            "REMEMBER: %d new bugs, %d new gaps, %d already known",
            new_bugs, new_gaps, skipped_known,
        )
        return summary

    def mark_gap_resolved(self, bug_key: str, issue_url: str) -> None:
        """Mark a gap as resolved with the created issue/PR URL."""
        for gap_key, gap in self._data["gaps"].items():
            if gap["bug_key"] == bug_key and gap["status"] == "open":
                gap["status"] = "resolved"
                gap["resolved_at"] = datetime.now().isoformat()
                gap["issue_url"] = issue_url
        self._save()

    def add_finding(self, agent_name: str, finding: str) -> None:
        """Record a learned finding for future reference."""
        self._data["findings"].append({
            "agent": agent_name,
            "finding": finding,
            "timestamp": datetime.now().isoformat(),
        })
        self._save()

    def get_open_gaps(self) -> list[dict]:
        """Get all unresolved gaps."""
        return [g for g in self._data["gaps"].values() if g.get("status") == "open"]

    def get_run_history(self) -> list[dict]:
        """Get all previous runs."""
        return self._data["runs"]

    def get_stats(self) -> dict:
        """Get memory statistics."""
        return {
            "total_bugs_analyzed": len(self._data["analyzed_bugs"]),
            "total_gaps": len(self._data["gaps"]),
            "open_gaps": len(self.get_open_gaps()),
            "total_findings": len(self._data["findings"]),
            "total_runs": len(self._data["runs"]),
        }
