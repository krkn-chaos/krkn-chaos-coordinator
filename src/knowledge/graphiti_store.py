"""Graphiti temporal knowledge graph integration for the REMEMBER phase.

Uses Ollama (Qwen 2.5 Coder 14B) as the LLM backend — no external API key needed.
Falls back to JSON memory store if Neo4j or Graphiti is unavailable.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from src.models import AgentResult, GapAnalysis

logger = logging.getLogger(__name__)


# Custom entity types for our domain
class BugEntity(BaseModel):
    """An OCPBUGS JIRA bug."""
    component: str | None = Field(None, description="OpenShift component affected")
    priority: str | None = Field(None, description="Bug priority")
    chaos_relevant: bool | None = Field(None, description="Whether this bug needs chaos testing")
    failure_mode: str | None = Field(None, description="Type of failure described")


class ComponentEntity(BaseModel):
    """An OpenShift cluster component."""
    domain: str | None = Field(None, description="Agent domain (control_plane, networking, etc.)")


class GapEntity(BaseModel):
    """A chaos test coverage gap."""
    confidence: int | None = Field(None, description="Confidence score 0-100")
    status: str | None = Field(None, description="open, resolved, or rejected")
    injection_method: str | None = Field(None, description="krkn injection type needed")


class ScenarioEntity(BaseModel):
    """A krkn chaos scenario."""
    plugin: str | None = Field(None, description="krkn plugin name")
    scenario_type: str | None = Field(None, description="Scenario type key")


class ActionEntity(BaseModel):
    """An action taken (issue created, PR opened)."""
    action_type: str | None = Field(None, description="issue or pr")
    url: str | None = Field(None, description="GitHub URL")


ENTITY_TYPES = {
    "Bug": BugEntity,
    "Component": ComponentEntity,
    "Gap": GapEntity,
    "Scenario": ScenarioEntity,
    "Action": ActionEntity,
}


class GraphitiStore:
    """Graphiti temporal knowledge graph backed by Neo4j + Ollama."""

    def __init__(
        self,
        neo4j_uri: str = "bolt://localhost:7687",
        neo4j_user: str = "neo4j",
        neo4j_password: str = "password",
        ollama_model: str = "qwen2.5-coder:14b",
    ):
        self._neo4j_uri = neo4j_uri
        self._neo4j_user = neo4j_user
        self._neo4j_password = neo4j_password
        self._ollama_model = ollama_model
        self._graphiti = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize Graphiti with Neo4j and Ollama.

        Uses OpenAIGenericClient which injects JSON schemas into prompts
        instead of relying on OpenAI's structured output API (which Ollama
        doesn't support).
        """
        try:
            from graphiti_core import Graphiti
            from graphiti_core.llm_client import LLMConfig
            from src.knowledge.ollama_llm_client import OllamaClient

            # Use our custom OllamaClient for Ollama compatibility
            llm_client = OllamaClient(
                LLMConfig(
                    api_key="not-needed",
                    model=self._ollama_model,
                    base_url="http://localhost:11434/v1",
                )
            )

            self._graphiti = Graphiti(
                self._neo4j_uri,
                self._neo4j_user,
                self._neo4j_password,
                llm_client=llm_client,
            )
            await self._graphiti.build_indices_and_constraints()
            self._initialized = True
            logger.info("Graphiti initialized with Neo4j + Ollama (%s)", self._ollama_model)
            return True

        except Exception as e:
            logger.warning("Graphiti initialization failed: %s. Falling back to JSON memory.", e)
            self._initialized = False
            return False

    async def remember_agent_result(self, result: AgentResult) -> dict:
        """Store an agent's full result in the knowledge graph."""
        if not self._initialized:
            logger.warning("Graphiti not initialized, skipping remember")
            return {"stored": False}

        from graphiti_core.nodes import EpisodeType

        timestamp = datetime.now(timezone.utc)
        stored_count = 0

        # Store each bug as an episode
        for bug in result.bugs_discovered:
            episode_text = (
                f"Bug {bug.key} was found in the {bug.component} component. "
                f"Priority: {bug.priority}. Status: {bug.status}. "
                f"Summary: {bug.summary}"
            )
            try:
                await self._graphiti.add_episode(
                    name=f"bug_{bug.key}",
                    episode_body=episode_text,
                    source=EpisodeType.text,
                    source_description=f"JIRA OCPBUGS scan by {result.agent_name}",
                    reference_time=timestamp,
                    entity_types=ENTITY_TYPES,
                    group_id=result.agent_name,
                )
                stored_count += 1
            except Exception as e:
                logger.warning("Failed to store bug %s: %s", bug.key, e)

        # Store gaps
        for gap in result.gaps:
            episode_text = (
                f"A chaos test coverage gap was identified for bug {gap.bug.key} "
                f"in the {gap.bug.component} component. "
                f"Confidence: {gap.confidence_score}/100 ({gap.confidence_level.value}). "
                f"Action: {gap.action_type.value}. "
                f"Reasoning: {gap.reasoning}. "
                f"Base scenario: {gap.base_scenario or 'none'}."
            )
            try:
                await self._graphiti.add_episode(
                    name=f"gap_{gap.bug.key}",
                    episode_body=episode_text,
                    source=EpisodeType.text,
                    source_description=f"Gap analysis by {result.agent_name}",
                    reference_time=timestamp,
                    entity_types=ENTITY_TYPES,
                    group_id=result.agent_name,
                )
                stored_count += 1
            except Exception as e:
                logger.warning("Failed to store gap for %s: %s", gap.bug.key, e)

        # Store filter decisions (skipped bugs)
        for fr in result.bugs_filtered_out:
            episode_text = (
                f"Bug {fr.bug.key} in {fr.bug.component} was filtered out as not chaos-relevant. "
                f"Reason: {fr.skip_reason}."
            )
            try:
                await self._graphiti.add_episode(
                    name=f"skip_{fr.bug.key}",
                    episode_body=episode_text,
                    source=EpisodeType.text,
                    source_description=f"Filter decision by {result.agent_name}",
                    reference_time=timestamp,
                    entity_types=ENTITY_TYPES,
                    group_id=result.agent_name,
                )
                stored_count += 1
            except Exception as e:
                logger.warning("Failed to store filter decision for %s: %s", fr.bug.key, e)

        logger.info("Graphiti REMEMBER: stored %d episodes for %s", stored_count, result.agent_name)
        return {"stored": True, "episodes": stored_count}

    async def remember_action(self, bug_key: str, action_type: str, url: str) -> None:
        """Record that an action was taken for a gap."""
        if not self._initialized:
            return

        from graphiti_core.nodes import EpisodeType

        episode_text = (
            f"A GitHub {action_type} was created for bug {bug_key}. URL: {url}. "
            f"The chaos test coverage gap has been addressed."
        )
        try:
            await self._graphiti.add_episode(
                name=f"action_{bug_key}",
                episode_body=episode_text,
                source=EpisodeType.text,
                source_description="ACT phase",
                reference_time=datetime.now(timezone.utc),
                entity_types=ENTITY_TYPES,
                group_id="actions",
            )
        except Exception as e:
            logger.warning("Failed to store action for %s: %s", bug_key, e)

    async def search(self, query: str, num_results: int = 10) -> list[dict]:
        """Search the knowledge graph."""
        if not self._initialized:
            return []

        try:
            results = await self._graphiti.search(query, num_results=num_results)
            return [
                {"fact": edge.fact, "source": str(edge.source_node_uuid),
                 "target": str(edge.target_node_uuid)}
                for edge in results
            ]
        except Exception as e:
            logger.warning("Graphiti search failed: %s", e)
            return []

    async def close(self) -> None:
        """Close Graphiti connection."""
        if self._graphiti:
            await self._graphiti.close()


def run_graphiti_remember(result: AgentResult) -> dict:
    """Synchronous wrapper for Graphiti remember."""
    store = GraphitiStore()

    async def _run():
        if not await store.initialize():
            return {"stored": False, "reason": "initialization_failed"}
        try:
            return await store.remember_agent_result(result)
        finally:
            await store.close()

    return asyncio.run(_run())
