"""Neo4j Direct knowledge graph for the REMEMBER phase.

Writes structured data directly to Neo4j via Cypher queries.
No LLM needed — our data is already structured (Bug, Gap, Action objects).
5ms per write instead of 30 seconds with Graphiti.
"""

import logging
from datetime import datetime, timezone

from neo4j import AsyncGraphDatabase

from src.models import AgentResult, FilterResult, GapAnalysis

logger = logging.getLogger(__name__)


class Neo4jStore:
    """Direct Neo4j knowledge graph — no LLM, no Graphiti."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "password",
    ):
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None

    async def connect(self) -> bool:
        """Connect to Neo4j and create schema."""
        try:
            self._driver = AsyncGraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            await self._create_schema()
            logger.info("Neo4j connected at %s", self._uri)
            return True
        except Exception as e:
            logger.warning("Neo4j connection failed: %s", e)
            return False

    async def _create_schema(self) -> None:
        """Create indices and constraints."""
        queries = [
            "CREATE INDEX IF NOT EXISTS FOR (b:Bug) ON (b.key)",
            "CREATE INDEX IF NOT EXISTS FOR (c:Component) ON (c.name)",
            "CREATE INDEX IF NOT EXISTS FOR (g:Gap) ON (g.id)",
            "CREATE INDEX IF NOT EXISTS FOR (a:Action) ON (a.url)",
            "CREATE INDEX IF NOT EXISTS FOR (f:Finding) ON (f.id)",
            "CREATE INDEX IF NOT EXISTS FOR (r:Run) ON (r.id)",
        ]
        async with self._driver.session() as session:
            for q in queries:
                try:
                    await session.run(q)
                except Exception:
                    pass  # Index may already exist

    async def remember_result(self, result: AgentResult) -> dict:
        """Store an agent run's results in the graph."""
        timestamp = datetime.now(timezone.utc).isoformat()
        new_bugs = 0
        new_gaps = 0

        async with self._driver.session() as session:
            # Store the run
            run_id = f"{result.agent_name}_{timestamp}"
            await session.run(
                """
                CREATE (r:Run {
                    id: $id, agent: $agent, timestamp: $ts,
                    bugs_discovered: $discovered, bugs_filtered: $filtered,
                    bugs_matched: $matched, gaps_found: $gaps
                })
                """,
                id=run_id, agent=result.agent_name, ts=timestamp,
                discovered=len(result.bugs_discovered),
                filtered=len(result.bugs_filtered_out),
                matched=len(result.bugs_matched),
                gaps=len(result.gaps),
            )

            # Store bugs + components
            for bug in result.bugs_discovered:
                r = await session.run(
                    """
                    MERGE (c:Component {name: $component})
                    MERGE (b:Bug {key: $key})
                    ON CREATE SET b.summary = $summary, b.priority = $priority,
                        b.status = $status, b.created = $created,
                        b.first_seen = $ts, b.url = $url
                    SET b.last_seen = $ts
                    MERGE (c)-[:HAS_BUG]->(b)
                    RETURN b.first_seen = $ts AS is_new
                    """,
                    key=bug.key, summary=bug.summary, component=bug.component,
                    priority=bug.priority, status=bug.status,
                    created=bug.created, url=bug.url, ts=timestamp,
                )
                record = await r.single()
                if record and record["is_new"]:
                    new_bugs += 1

            # Store filter decisions (skipped bugs)
            for fr in result.bugs_filtered_out:
                await session.run(
                    """
                    MERGE (b:Bug {key: $key})
                    SET b.chaos_relevant = false, b.skip_reason = $reason
                    """,
                    key=fr.bug.key, reason=fr.skip_reason,
                )

            # Store gaps
            for gap in result.gaps:
                gap_id = f"{gap.bug.key}_{result.agent_name}"
                r = await session.run(
                    """
                    MATCH (b:Bug {key: $bug_key})
                    MERGE (g:Gap {id: $gap_id})
                    ON CREATE SET g.confidence = $confidence,
                        g.confidence_level = $level,
                        g.action_type = $action_type,
                        g.reasoning = $reasoning,
                        g.base_scenario = $base_scenario,
                        g.status = 'open',
                        g.opened_at = $ts,
                        g.agent = $agent
                    MERGE (b)-[:HAS_GAP]->(g)
                    RETURN g.opened_at = $ts AS is_new
                    """,
                    bug_key=gap.bug.key, gap_id=gap_id,
                    confidence=gap.confidence_score,
                    level=gap.confidence_level.value,
                    action_type=gap.action_type.value,
                    reasoning=gap.reasoning,
                    base_scenario=gap.base_scenario,
                    ts=timestamp, agent=result.agent_name,
                )
                record = await r.single()
                if record and record["is_new"]:
                    new_gaps += 1

            # Link run to agent
            await session.run(
                """
                MERGE (a:Agent {name: $agent})
                WITH a
                MATCH (r:Run {id: $run_id})
                MERGE (a)-[:PERFORMED]->(r)
                """,
                agent=result.agent_name, run_id=run_id,
            )

        logger.info("Neo4j REMEMBER: %d new bugs, %d new gaps", new_bugs, new_gaps)
        return {"new_bugs": new_bugs, "new_gaps": new_gaps}

    async def mark_gap_resolved(self, bug_key: str, issue_url: str) -> None:
        """Mark a gap as resolved with the created issue/PR URL."""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (b:Bug {key: $key})-[:HAS_GAP]->(g:Gap {status: 'open'})
                SET g.status = 'resolved', g.resolved_at = $ts
                CREATE (a:Action {type: 'issue', url: $url, created_at: $ts})
                MERGE (g)-[:RESOLVED_BY]->(a)
                """,
                key=bug_key, url=issue_url,
                ts=datetime.now(timezone.utc).isoformat(),
            )

    async def add_finding(self, agent_name: str, finding: str) -> None:
        """Record a learned finding."""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (a:Agent {name: $agent})
                CREATE (f:Finding {
                    id: $id, text: $finding, created_at: $ts
                })
                MERGE (a)-[:LEARNED]->(f)
                """,
                agent=agent_name, finding=finding,
                id=f"{agent_name}_{datetime.now(timezone.utc).isoformat()}",
                ts=datetime.now(timezone.utc).isoformat(),
            )

    async def is_bug_analyzed(self, bug_key: str) -> bool:
        """Check if a bug was already analyzed."""
        async with self._driver.session() as session:
            r = await session.run(
                "MATCH (b:Bug {key: $key}) RETURN b.key AS key",
                key=bug_key,
            )
            return await r.single() is not None

    async def get_analyzed_bug_keys(self) -> set[str]:
        """Get all previously analyzed bug keys."""
        async with self._driver.session() as session:
            r = await session.run("MATCH (b:Bug) RETURN b.key AS key")
            records = [record async for record in r]
            return {record["key"] for record in records}

    async def get_open_gaps(self) -> list[dict]:
        """Get all unresolved gaps."""
        async with self._driver.session() as session:
            r = await session.run(
                """
                MATCH (b:Bug)-[:HAS_GAP]->(g:Gap {status: 'open'})
                RETURN b.key AS bug_key, b.summary AS summary,
                       b.component AS component, g.confidence AS confidence,
                       g.reasoning AS reasoning, g.opened_at AS opened_at
                ORDER BY g.confidence DESC
                """
            )
            return [dict(record) async for record in r]

    async def get_component_gap_counts(self) -> list[dict]:
        """Get gap counts per component — for trend detection."""
        async with self._driver.session() as session:
            r = await session.run(
                """
                MATCH (c:Component)-[:HAS_BUG]->(b)-[:HAS_GAP]->(g)
                RETURN c.name AS component, count(g) AS gaps,
                       sum(CASE WHEN g.status = 'open' THEN 1 ELSE 0 END) AS open_gaps,
                       sum(CASE WHEN g.status = 'resolved' THEN 1 ELSE 0 END) AS resolved_gaps
                ORDER BY gaps DESC
                """
            )
            return [dict(record) async for record in r]

    async def get_uncovered_components(self) -> list[dict]:
        """Find components with bugs but no chaos scenarios."""
        async with self._driver.session() as session:
            r = await session.run(
                """
                MATCH (c:Component)-[:HAS_BUG]->(b)
                WHERE NOT (c)<-[:COVERS]-(:Scenario)
                RETURN c.name AS component, count(b) AS bug_count
                ORDER BY bug_count DESC
                """
            )
            return [dict(record) async for record in r]

    async def get_run_history(self, limit: int = 20) -> list[dict]:
        """Get recent run history for trend tracking."""
        async with self._driver.session() as session:
            r = await session.run(
                """
                MATCH (r:Run)
                RETURN r.agent AS agent, r.timestamp AS timestamp,
                       r.bugs_discovered AS discovered, r.gaps_found AS gaps
                ORDER BY r.timestamp DESC
                LIMIT $limit
                """,
                limit=limit,
            )
            return [dict(record) async for record in r]

    async def get_similar_resolved_bugs(self, component: str) -> list[dict]:
        """Find resolved bugs in the same component — for recommendations."""
        async with self._driver.session() as session:
            r = await session.run(
                """
                MATCH (c:Component {name: $component})-[:HAS_BUG]->(b)
                      -[:HAS_GAP]->(g {status: 'resolved'})-[:RESOLVED_BY]->(a)
                RETURN b.key AS bug_key, b.summary AS summary,
                       a.url AS issue_url, g.reasoning AS reasoning
                """,
                component=component,
            )
            return [dict(record) async for record in r]

    async def close(self) -> None:
        """Close Neo4j connection."""
        if self._driver:
            await self._driver.close()
