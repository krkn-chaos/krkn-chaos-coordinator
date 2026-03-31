# krkn-chaos-coordinator

AI-driven multi-agent system that autonomously expands [krkn](https://github.com/krkn-chaos/krkn) chaos test coverage for OpenShift clusters by monitoring JIRA bugs and Sippy regressions, identifying coverage gaps, and creating PRs/issues.

## How It Works

```
DISCOVER → FILTER → MAP → ANALYZE → ACT → REMEMBER

1. DISCOVER   Query JIRA + Sippy for new bugs and regressions
2. FILTER     Is this a failure mode krkn can chaos-test? (Claude Code / Ollama / keyword)
3. MAP        Do we already have a scenario for this? (ChromaDB semantic search)
4. ANALYZE    Score confidence, determine if scenario exists to extend
5. ACT        Create GitHub issues or draft PRs with detailed next steps
6. REMEMBER   Track analyzed bugs so they're not re-processed
```

## Architecture

```
Orchestrator
├── Upgrade & Lifecycle    (CVO, MCO, Installer)
├── Control Plane          (etcd, kube-apiserver, scheduler)
├── Node & Machine         (kubelet, Machine API, Cloud Compute)
├── Networking             (OVN-K, DNS, router, ingress)
├── Storage                (CSI, Image Registry)
└── Operators & Platform   (OLM, Console, Auth, Monitoring)
```

6 domain agents, each covering a set of OCPBUGS components. All share the same pipeline.

## Knowledge Base

4,089 chunks across 3 ChromaDB collections, pulled from GitHub:

| Collection | Chunks | Sources |
|-----------|--------|---------|
| scenario_docs | 65 | krkn scenario YAMLs + plugin code |
| krkn_docs | 750 | Website + krkn-hub + krkn-lib API + CLAUDE.md |
| ocp_docs | 3,274 | OpenShift modules + topic assemblies |

## Quick Start

### Prerequisites

- Python 3.11+
- Podman or Docker (for Neo4j)
- Ollama with llama3 (optional, for LLM filter)
- JIRA API token + GitHub PAT

### Setup

```bash
# Clone
git clone https://github.com/shahsahil264/krkn-chaos-coordinator.git
cd krkn-chaos-coordinator

# Virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Environment variables
cp .env.example .env
# Edit .env with your JIRA_API_TOKEN, JIRA_USERNAME, GITHUB_TOKEN

# Start Neo4j (optional, for Graphiti memory)
podman run -d --name neo4j-coordinator \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5-community

# Ingest knowledge base (one-time, ~6 min)
PYTHONPATH=. python -m src.knowledge.ingest ./chroma_data
```

### Run

#### Option 1: Claude Code (recommended)

```bash
cd ~/krkn-chaos-coordinator
claude
# Then type: /run-scan
```

Claude Code does the FILTER step with its own reasoning -- best quality.

#### Option 2: Streamlit Dashboard

```bash
PYTHONPATH=. streamlit run src/ui/web_dashboard.py --server.port 8501
# Open http://localhost:8501
# Click "LAUNCH SCAN"
```

Web dashboard with Approve/Reject buttons, charts, and 7 tabs.

#### Option 3: Terminal UI

```bash
PYTHONPATH=. python -m src.run_with_ui
```

Rich terminal dashboard with live pipeline animation.

#### Option 4: CLI

```bash
# Single agent
PYTHONPATH=. python src/main.py --release 4.21 --agent control_plane

# All agents
PYTHONPATH=. python src/main.py --release 4.21
```

### Run Tests

```bash
PYTHONPATH=. pytest tests/unit/ -v        # 40 tests
PYTHONPATH=. pytest tests/ -v --cov=src   # With coverage
```

## Project Structure

```
src/
├── main.py                        # CLI entry point
├── models.py                      # Domain models
├── run_with_ui.py                 # Terminal UI runner
├── run_pipeline.py                # Pipeline runner (JSON input)
├── run_filtered.py                # Pre-filtered pipeline (Claude Code mode)
├── apis/
│   ├── jira_client.py             # JIRA REST API
│   ├── sippy_client.py            # Sippy regressions + health
│   └── github_client.py           # GitHub API (issues, PRs)
├── knowledge/
│   ├── chromadb_store.py          # Vector search (3 collections)
│   ├── component_map.py           # 6 agents → OCPBUGS components
│   ├── scenario_index.py          # Index krkn scenario YAMLs
│   ├── ingest.py                  # Full ingestion from GitHub
│   └── memory.py                  # REMEMBER phase (JSON/Graphiti)
├── filter/
│   ├── chaos_filter.py            # Keyword-based filter
│   └── llm_filter.py              # Ollama LLM filter
├── agents/
│   ├── base_agent.py              # Pipeline: DISCOVER→FILTER→MAP→ANALYZE
│   ├── control_plane_agent.py     # Control Plane agent
│   ├── upgrade_lifecycle_agent.py # Upgrade & Lifecycle agent
│   ├── node_machine_agent.py      # Node & Machine agent
│   ├── networking_agent.py        # Networking agent
│   ├── storage_agent.py           # Storage agent
│   ├── operators_platform_agent.py # Operators & Platform agent
│   ├── act.py                     # GitHub issue creation
│   ├── pr_creator.py              # Draft PR creation on forks
│   ├── hub_generator.py           # krkn-hub boilerplate generator
│   └── docs_generator.py          # Website docs generator
├── coordinator/
│   └── orchestrator.py            # Dedup, approval queue
└── ui/
    ├── terminal_ui.py             # Rich terminal dashboard
    └── web_dashboard.py           # Streamlit web dashboard
```

## Filter Modes

| Mode | Engine | Quality | Speed |
|------|--------|---------|-------|
| Claude Code (`/run-scan`) | Claude's reasoning | Best | Interactive |
| Ollama (dashboard toggle) | llama3 local | Good | ~3-5s/bug |
| Keyword (default) | Pattern matching | OK | Instant |

## ADR

Design document: [Confluence](https://redhat.atlassian.net/wiki/x/x4rTFg)

## License

Apache-2.0
