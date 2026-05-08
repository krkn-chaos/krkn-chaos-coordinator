"""Stratified bug sampler for eval runs.

Reads coordinator_memory.json and returns a reproducible, stratified
sample of Bug objects proportional to each agent domain.
"""

import json
import logging
import math
import random
from collections import defaultdict

from src.models import Bug

logger = logging.getLogger(__name__)


def sample_bugs_for_eval(
    memory_path: str = "./coordinator_memory.json",
    sample_size: int = 200,
    seed: int = 42,
) -> list[Bug]:
    """Sample bugs stratified by agent domain for eval.

    Reads coordinator_memory.json, extracts bug keys and metadata,
    creates Bug objects, and returns a stratified sample.

    Stratification: proportional to bugs per agent domain.

    Args:
        memory_path: Path to the coordinator_memory.json file.
        sample_size: Total number of bugs to sample.
        seed: Random seed for reproducibility.

    Returns:
        List of Bug objects sampled proportionally across agent domains.

    Raises:
        FileNotFoundError: If memory_path does not exist.
        ValueError: If sample_size exceeds available bugs or is non-positive.
    """
    with open(memory_path) as f:
        data = json.load(f)

    analyzed_bugs = data.get("analyzed_bugs", {})
    if not analyzed_bugs:
        return []

    total_bugs = len(analyzed_bugs)
    if sample_size <= 0:
        raise ValueError(f"sample_size must be positive, got {sample_size}")
    if sample_size > total_bugs:
        raise ValueError(
            f"sample_size ({sample_size}) exceeds available bugs ({total_bugs})"
        )

    # Group bug keys by agent domain
    bugs_by_agent: dict[str, list[str]] = defaultdict(list)
    for key, entry in analyzed_bugs.items():
        agent = entry.get("agent", "unknown")
        bugs_by_agent[agent] = [*bugs_by_agent[agent], key]

    rng = random.Random(seed)

    # Calculate proportional allocation per agent
    allocation: dict[str, int] = {}
    remaining = sample_size
    agents_sorted = sorted(bugs_by_agent.keys())

    for i, agent in enumerate(agents_sorted):
        agent_count = len(bugs_by_agent[agent])
        if i == len(agents_sorted) - 1:
            # Last agent gets the remainder to avoid rounding drift
            allocation[agent] = remaining
        else:
            share = math.floor(sample_size * agent_count / total_bugs)
            # Ensure at least 1 bug per agent if they have bugs
            share = max(1, min(share, agent_count))
            allocation[agent] = share
            remaining -= share

    logger.info(
        "Sampling %d bugs from %d total across %d agents: %s",
        sample_size,
        total_bugs,
        len(agents_sorted),
        {a: allocation[a] for a in agents_sorted},
    )

    # Sample from each agent group
    sampled_bugs: list[Bug] = []
    for agent in agents_sorted:
        keys = bugs_by_agent[agent]
        n = min(allocation[agent], len(keys))
        selected_keys = rng.sample(keys, n)

        for key in selected_keys:
            entry = analyzed_bugs[key]
            bug = Bug(
                key=key,
                summary=entry.get("summary", ""),
                description="",  # Not stored in memory; eval re-fetches if needed
                component=entry.get("component", ""),
                priority="",
                status="",
                created=entry.get("analyzed_at", ""),
                url=f"https://issues.redhat.com/browse/{key}",
            )
            sampled_bugs = [*sampled_bugs, bug]

    return sampled_bugs
