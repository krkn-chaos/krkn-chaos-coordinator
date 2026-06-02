# CLAUDE.md - krkn-chaos-coordinator

## Project Overview

AI-driven multi-agent system that expands krkn chaos test coverage for OpenShift by monitoring JIRA bugs and Sippy regressions, identifying coverage gaps, and creating PRs/issues.

## Architecture

- **1 Lightweight Orchestrator** — spawns agents, deduplicates, presents approval queue
- **6 Domain Agents** — each covers an OpenShift component area (96 OCPBUGS components total)
- **Pipeline**: DISCOVER → FILTER → MAP → ANALYZE → ACT → REMEMBER
- **Knowledge**: ChromaDB (docs/scenarios) + Neo4j (operational memory graph)
- **LLM**: 5 pluggable providers (claude_code, anthropic, ollama, openai, google) with per-phase model routing

## Repository Structure

```
krkn-chaos-coordinator/
├── src/
│   ├── main.py                    # Entry point (multi-version, multi-agent)
│   ├── models.py                  # Domain models (Bug, Gap, Observation, RunMetrics)
│   ├── reasoning.py               # LLM reasoning for MAP and ANALYZE
│   ├── logging_util.py            # Structured JSON logging
│   ├── coordinator/
│   │   └── orchestrator.py        # Dedup, format, approval queue
│   ├── agents/
│   │   ├── base_agent.py          # Base pipeline (DISCOVER→REMEMBER)
│   │   └── control_plane_agent.py # + 5 other domain agents
│   ├── apis/
│   │   ├── jira_client.py         # JIRA REST API (three-tier version query)
│   │   ├── sippy_client.py        # Sippy public API client
│   │   ├── github_client.py       # GitHub API client
│   │   └── release_client.py      # Z-stream changelog enrichment
│   ├── knowledge/
│   │   ├── chromadb_store.py      # Vector search for docs
│   │   ├── neo4j_store.py         # Graph memory (single backend, fail-fast)
│   │   ├── component_map.py       # Agent → OCPBUGS component mapping
│   │   ├── scenario_index.py      # Index krkn scenario YAML files
│   │   ├── filter_cache.py        # Semantic filter cache (Cache-Aside)
│   │   └── scenario_knowledgebase.py # krkn-knowledgebase integration
│   ├── filter/
│   │   ├── chaos_filter.py        # Keyword filter (167 keywords, confidence scoring)
│   │   ├── llm_filter.py          # LLM filter (5 providers, token tracking)
│   │   ├── llm_config.py          # Per-phase model routing + auto-detection
│   │   ├── llm_tools.py           # Typed tool functions with Observation returns
│   │   └── llm_batch.py           # Anthropic Batch API support
│   └── evals/
│       ├── filter_eval.py         # Model comparison eval
│       ├── sampler.py             # Stratified bug sampler
│       └── eval_report.py         # Eval metrics + pass criteria
├── tests/
│   ├── unit/                      # 175 unit tests
│   └── integration/               # 13 Neo4j integration tests
├── docker-compose.yaml            # Neo4j for graph memory
└── pyproject.toml                 # Project config
```

## Quick Start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Copy and fill in environment variables
cp .env.example .env

# Start Neo4j (required)
podman start neo4j-coordinator

# Run tests
PYTHONPATH=. pytest tests/ -v

# Run the coordinator
PYTHONPATH=. python src/main.py --release 4.21 --agent control_plane --use-llm

# Multiple agents/versions
PYTHONPATH=. python src/main.py --release 4.20,4.21 --agent control_plane,networking --use-llm

# All agents
PYTHONPATH=. python src/main.py --release 4.21 --use-llm
```

## Key Concepts

### Three-Tier FILTER
1. **Keyword pre-filter** — 167 chaos keywords, catches ~55% (zero tokens)
2. **Semantic cache** — ChromaDB cosine similarity on past decisions (zero tokens)
3. **LLM classification** — Sonnet with auto-escalation to Opus when confidence < 80

### Three-Tier JIRA Version Query
When `--release 4.21` is set:
- **Tier 1**: bugs tagged with 4.21.* (`affectedVersion >= 4.21 AND < 4.22`)
- **Tier 2**: open bugs from older versions (unfixed, likely still present)
- **Tier 3**: bugs with no `affectedVersion` set

### Confidence Scoring
- 70-100 (HIGH): Draft PRs across krkn + krkn-hub + website
- 40-69 (MEDIUM): GitHub issue with recommendation
- 0-39 (LOW): GitHub issue describing gap

### Component Mapping
Uses `team_component_map.json` from openshift-eng/ai-helpers for authoritative OCPBUGS component names. 96 components mapped across 6 agents.

### Token Optimization
claude_code provider uses `--bare --system-prompt --exclude-dynamic-system-prompt-sections` to strip Claude Code's 62K system prompt overhead. Per-call: ~2,700 tokens. Per-call usage logged: `LLM CALL #N: X in + Y out = Z tokens, $cost`.

## Documentation

- [Project Overview](docs/presentation.html) — Interactive visual guide (open in browser)
- [Design Spec](docs/superpowers/specs/2026-05-08-memory-and-token-optimization-design.md) — Full architecture spec

## Dependencies

- Python 3.11+
- ChromaDB for vector search
- Neo4j for knowledge graph (required, fail-fast at startup)
- JIRA API token, GitHub PAT

## Testing

```bash
PYTHONPATH=. pytest tests/unit/ -v              # 175 unit tests
PYTHONPATH=. pytest tests/integration/ -v       # 13 integration tests (requires Neo4j)
PYTHONPATH=. pytest tests/ -v                   # All 188 tests

# Run filter eval
PYTHONPATH=. python -m src.evals.filter_eval --sample-size 20 --provider claude_code
```

## Git Workflow

- Feature branches: `feat/<description>`
- Conventional commits: `feat:`, `fix:`, `test:`, `docs:`
- PRs from `shahsahil264/krkn-chaos-coordinator` → future `krkn-chaos/krkn-chaos-coordinator`
