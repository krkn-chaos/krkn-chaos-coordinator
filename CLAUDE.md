# CLAUDE.md - krkn-chaos-coordinator

## Project Overview

AI-driven multi-agent system that expands krkn chaos test coverage for OpenShift by monitoring JIRA bugs and Sippy regressions, identifying coverage gaps, and creating PRs/issues.

## Architecture

- **1 Lightweight Orchestrator** — spawns agents, deduplicates, presents approval queue
- **6 Domain Agents** — each covers an OpenShift component area
- **Pipeline**: DISCOVER → FILTER → MAP → ANALYZE → ACT → REMEMBER
- **Knowledge**: ChromaDB (docs) + Graphiti/Neo4j (operational memory) + direct APIs

## Repository Structure

```
krkn-chaos-coordinator/
├── src/
│   ├── main.py                    # Entry point
│   ├── models.py                  # Domain models (Bug, Gap, etc.)
│   ├── coordinator/
│   │   └── orchestrator.py        # Dedup, format, approval queue
│   ├── agents/
│   │   ├── base_agent.py          # Base pipeline (DISCOVER→REMEMBER)
│   │   └── control_plane_agent.py # Control Plane domain agent
│   ├── apis/
│   │   ├── jira_client.py         # JIRA REST API client
│   │   ├── sippy_client.py        # Sippy public API client
│   │   └── github_client.py       # GitHub API client
│   ├── knowledge/
│   │   ├── chromadb_store.py      # Vector search for docs
│   │   ├── component_map.py       # Agent → OCPBUGS component mapping
│   │   └── scenario_index.py      # Index krkn scenario YAML files
│   └── filter/
│       └── chaos_filter.py        # Chaos relevance filter
├── tests/
│   ├── unit/                      # Unit tests
│   └── integration/               # Integration tests
├── config/                        # Configuration files
├── docker-compose.yaml            # Neo4j for Graphiti
└── pyproject.toml                 # Project config
```

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Copy and fill in environment variables
cp .env.example .env

# Run tests
PYTHONPATH=. pytest tests/ -v

# Run the coordinator (Control Plane agent only)
PYTHONPATH=. python src/main.py --release 4.21 --agent control_plane
```

## Key Concepts

### Chaos Relevance Filter
Not every bug needs a chaos test. The filter checks:
1. Is this a resilience failure mode? (vs code bug, CVE, UI issue)
2. Can krkn inject this failure condition?

### Confidence Scoring
- 70-100 (HIGH): Draft PRs across krkn + krkn-hub + website
- 40-69 (MEDIUM): GitHub issue with recommendation
- 0-39 (LOW): GitHub issue describing gap

### Component Mapping
Uses `team_component_map.json` from openshift-eng/ai-helpers for authoritative OCPBUGS component names.

## Documentation

- [Project Overview](docs/project-overview.html) — Interactive visual guide covering architecture, pipeline, filter system, knowledge layer, and FAQ. Open in a browser.

## Dependencies

- Python 3.11+
- ChromaDB for vector search
- Graphiti + Neo4j for knowledge graph (Phase 2)
- JIRA API token, GitHub PAT

## Testing

```bash
PYTHONPATH=. pytest tests/unit/ -v          # Unit tests
PYTHONPATH=. pytest tests/ -v --cov=src     # With coverage
```

## Git Workflow

- Feature branches: `feat/<description>`
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`
- PRs from `shahsahil264/krkn-chaos-coordinator` → future `krkn-chaos/krkn-chaos-coordinator`
