"""Migrate coordinator_memory.json data to Neo4j.

Reads analyzed bugs and gaps from the JSON memory file and imports them
into Neo4j as Bug and Gap nodes, preserving the existing schema used by
Neo4jStore.

Usage:
    PYTHONPATH=. python src/scripts/migrate_json_to_neo4j.py
    PYTHONPATH=. python src/scripts/migrate_json_to_neo4j.py --json-path ./coordinator_memory.json
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from neo4j import GraphDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_JSON_PATH = Path("./coordinator_memory.json")


def load_json_memory(path: Path) -> dict:
    """Load and validate the JSON memory file."""
    if not path.exists():
        logger.error("JSON memory file not found: %s", path)
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    required_keys = {"analyzed_bugs", "gaps"}
    missing = required_keys - set(data.keys())
    if missing:
        logger.error("JSON memory file missing keys: %s", missing)
        sys.exit(1)

    logger.info(
        "Loaded JSON memory: %d bugs, %d gaps",
        len(data["analyzed_bugs"]),
        len(data["gaps"]),
    )
    return data


def connect_neo4j(uri: str, user: str, password: str) -> GraphDatabase.driver:
    """Connect to Neo4j and verify connectivity."""
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    logger.info("Connected to Neo4j at %s", uri)
    return driver


def create_schema(driver: GraphDatabase.driver) -> None:
    """Ensure indexes exist for Bug and Gap nodes."""
    queries = [
        "CREATE INDEX IF NOT EXISTS FOR (b:Bug) ON (b.key)",
        "CREATE INDEX IF NOT EXISTS FOR (c:Component) ON (c.name)",
        "CREATE INDEX IF NOT EXISTS FOR (g:Gap) ON (g.id)",
    ]
    with driver.session() as session:
        for q in queries:
            session.run(q)
    logger.info("Schema indexes verified")


def migrate_bugs(driver: GraphDatabase.driver, analyzed_bugs: dict) -> int:
    """Import analyzed bugs as Bug nodes with Component relationships.

    Returns the number of bugs imported.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    imported = 0

    with driver.session() as session:
        for bug_key, bug_data in analyzed_bugs.items():
            component = bug_data.get("component", "Unknown")
            session.run(
                """
                MERGE (b:Bug {key: $key})
                ON CREATE SET
                    b.summary = $summary,
                    b.priority = $priority,
                    b.status = $status,
                    b.first_seen = $analyzed_at,
                    b.last_seen = $timestamp,
                    b.chaos_relevant = $chaos_relevant,
                    b.skip_reason = $skip_reason,
                    b.migrated_from_json = true
                ON MATCH SET
                    b.last_seen = $timestamp
                """,
                key=bug_key,
                summary=bug_data.get("summary", ""),
                priority=bug_data.get("priority", "Unknown"),
                status=bug_data.get("status", "Unknown"),
                analyzed_at=bug_data.get("analyzed_at", timestamp),
                timestamp=timestamp,
                chaos_relevant=bug_data.get("chaos_relevant", True),
                skip_reason=bug_data.get("skip_reason"),
            )

            # Link bug to component
            session.run(
                """
                MERGE (c:Component {name: $component})
                MERGE (b:Bug {key: $key})
                MERGE (c)-[:HAS_BUG]->(b)
                """,
                component=component,
                key=bug_key,
            )

            imported += 1
            if imported % 100 == 0:
                logger.info("  Bugs imported: %d / %d", imported, len(analyzed_bugs))

    logger.info("Bug migration complete: %d bugs imported", imported)
    return imported


def migrate_gaps(driver: GraphDatabase.driver, gaps: dict) -> int:
    """Import gaps as Gap nodes linked to their Bug nodes.

    Returns the number of gaps imported.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    imported = 0

    with driver.session() as session:
        for gap_key, gap_data in gaps.items():
            bug_key = gap_data.get("bug_key", "")
            if not bug_key:
                logger.warning("Gap %s has no bug_key, skipping", gap_key)
                continue

            session.run(
                """
                MERGE (b:Bug {key: $bug_key})
                WITH b
                MERGE (g:Gap {id: $gap_id})
                ON CREATE SET
                    g.confidence = $confidence,
                    g.action_type = $action_type,
                    g.reasoning = $reasoning,
                    g.base_scenario = $base_scenario,
                    g.status = $status,
                    g.agent = $agent,
                    g.opened_at = $created_at,
                    g.migrated_from_json = true
                MERGE (b)-[:HAS_GAP]->(g)
                """,
                bug_key=bug_key,
                gap_id=gap_key,
                confidence=gap_data.get("confidence", 0),
                action_type=gap_data.get("action_type", "github_issue"),
                reasoning=gap_data.get("reasoning", ""),
                base_scenario=gap_data.get("base_scenario"),
                status=gap_data.get("status", "open"),
                agent=gap_data.get("agent", "unknown"),
                created_at=gap_data.get("created_at", timestamp),
            )

            imported += 1
            if imported % 100 == 0:
                logger.info("  Gaps imported: %d / %d", imported, len(gaps))

    logger.info("Gap migration complete: %d gaps imported", imported)
    return imported


def validate_counts(
    driver: GraphDatabase.driver,
    expected_bugs: int,
    expected_gaps: int,
) -> bool:
    """Validate that Neo4j node counts match expected values."""
    with driver.session() as session:
        bug_count = session.run("MATCH (b:Bug) RETURN count(b) AS count").single()["count"]
        gap_count = session.run("MATCH (g:Gap) RETURN count(g) AS count").single()["count"]

    logger.info("Validation - Bugs: %d (expected >= %d)", bug_count, expected_bugs)
    logger.info("Validation - Gaps: %d (expected >= %d)", gap_count, expected_gaps)

    if bug_count < expected_bugs:
        logger.error(
            "Bug count mismatch: got %d, expected at least %d",
            bug_count, expected_bugs,
        )
        return False

    if gap_count < expected_gaps:
        logger.error(
            "Gap count mismatch: got %d, expected at least %d",
            gap_count, expected_gaps,
        )
        return False

    logger.info("Validation passed")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate JSON memory to Neo4j")
    parser.add_argument(
        "--json-path",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Path to coordinator_memory.json",
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j URI (default: bolt://localhost:7687)",
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.environ.get("NEO4J_USER", "neo4j"),
        help="Neo4j user (default: neo4j)",
    )
    args = parser.parse_args()

    neo4j_password = os.environ.get("NEO4J_PASSWORD")
    if not neo4j_password:
        logger.error("NEO4J_PASSWORD environment variable is required")
        sys.exit(1)

    # Load JSON data
    data = load_json_memory(args.json_path)

    # Connect to Neo4j
    driver = connect_neo4j(args.neo4j_uri, args.neo4j_user, neo4j_password)

    try:
        create_schema(driver)

        # Migrate bugs
        bugs_imported = migrate_bugs(driver, data["analyzed_bugs"])

        # Migrate gaps
        gaps_imported = migrate_gaps(driver, data["gaps"])

        # Validate
        valid = validate_counts(driver, bugs_imported, gaps_imported)

        if valid:
            logger.info(
                "Migration complete: %d bugs, %d gaps imported successfully",
                bugs_imported, gaps_imported,
            )
        else:
            logger.error("Migration completed with validation errors")
            sys.exit(1)

    finally:
        driver.close()


if __name__ == "__main__":
    main()
