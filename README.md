# krkn-chaos-coordinator

AI-driven multi-agent system that autonomously expands [krkn](https://github.com/krkn-chaos/krkn) chaos test coverage for OpenShift clusters by monitoring JIRA bugs, identifying coverage gaps, and creating PRs/issues.

## How It Works

```
DISCOVER → FILTER → MAP → ANALYZE → ACT → REMEMBER

1. DISCOVER   Query JIRA (4-tier version matching) + z-stream changelogs
2. FILTER     3-tier: keyword pre-filter → semantic cache → LLM classification
3. MAP        ChromaDB RAG + LLM reasoning over existing krkn scenarios
4. ANALYZE    Score confidence (0-100), generate specific krkn modifications
5. ACT        Create GitHub issues (MEDIUM) or draft PRs (HIGH confidence)
6. REMEMBER   Store in Neo4j graph — never re-analyze the same bug
```

## Architecture

```
Orchestrator (dedup, approval queue)
├── Control Plane          (Etcd, kube-apiserver, HyperShift)
├── Networking             (OVN-K, DNS, router, SR-IOV, MetalLB)
├── Node & Machine         (Kubelet, CRI-O, Machine API, Bare Metal)
├── Storage                (CSI, Image Registry, LVMS)
├── Operators & Platform   (OLM, Console, Auth, Monitoring, Cloud Compute)
├── Upgrade & Lifecycle    (CVO, MCO, Installer variants)
└── <your agent here>      (drop a YAML in config/agents/)
```

Pluggable agents — auto-discovered from `config/agents/*.yaml`. 6 built-in agents covering 113 OCPBUGS components. Drop a YAML file to add a new domain.

## Knowledge Layer

| Store | Purpose | Data |
|-------|---------|------|
| **ChromaDB** | Vector search (RAG context for LLM) | 4,089+ chunks: krkn scenarios, krkn docs, OCP docs, agent-specific docs, filter cache |
| **Neo4j** | Operational memory (dedup, history) | 3,000+ bugs, 484+ gaps, component relationships, run metrics |

## JIRA Version Query (4-Tier)

When `--release 4.21` is set, bugs are fetched using 4-tier matching to catch everything:

| Tier | What it catches | JQL Filter |
|------|----------------|------------|
| 1 | Exact release match | `affectedVersion >= "4.21" AND < "4.22"` (catches 4.21, 4.21.0, 4.21.z, 4.21.5, etc.) |
| 2 | Older versions, still open | `affectedVersion < "4.21" AND status NOT IN (Closed, Verified)` |
| 3 | Newer versions, still open | `affectedVersion >= "4.22" AND status NOT IN (Closed, Verified)` (if it exists on 5.0, it exists on 4.21 too) |
| 4 | No version set | `affectedVersion IS EMPTY` |

Closed/Verified bugs on other versions are correctly excluded — they're already fixed.

## LLM Providers

5 pluggable backends, configurable per-phase:

| Provider | Description | API Key Required |
|----------|-------------|-----------------|
| `claude_code` | Claude Code CLI — uses your existing subscription | No (auto-detected when `claude` is on PATH) |
| `anthropic` | Direct API with prompt caching + batch API | Yes (`ANTHROPIC_API_KEY`) |
| `ollama` | Local models (qwen2.5-coder, llama3, etc.) | No (auto-detected when running) |
| `openai` | GPT-4o compatible | Yes (`OPENAI_API_KEY`) |
| `google` | Gemini compatible | Yes (`GOOGLE_API_KEY`) |

Per-phase model routing: `LLM_FILTER_MODEL=claude-sonnet-4-6`, `LLM_ANALYZE_MODEL=claude-opus-4-6`

## Token Optimization

6-layer stack reduces cost by 91%:

1. **Keyword pre-filter** — configurable keywords in `config/filters/common.yaml` + per-agent overrides, catches ~55% (zero tokens)
2. **Semantic cache** — ChromaDB cosine similarity on past decisions (zero tokens)
3. **Model routing** — Sonnet for FILTER/MAP, Opus for ANALYZE
4. **Confidence escalation** — Sonnet → Opus only when uncertain (<80)
5. **Prompt caching** — `cache_control` on system prompts (90% off)
6. **Batch API** — 50% off, stacks with caching

With `claude_code` provider: `--bare --system-prompt` strips 62K system prompt overhead → ~2,700 tokens per call.

---

## Setup

### Quick Setup (recommended)

```bash
git clone https://github.com/shahsahil264/krkn-chaos-coordinator.git
cd krkn-chaos-coordinator
./setup.sh
```

# Clone the krkn repo locally (required for scenario indexing)
git clone https://github.com/krkn-chaos/krkn ~/krkn

# Virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Environment variables
cp .env.example .env
# Fill in your values — at minimum:
#   JIRA_USERNAME=your-email@redhat.com
#   JIRA_API_TOKEN=your-jira-api-token
#   GITHUB_TOKEN=your-github-token
#   KRKN_REPO_URL=https://github.com/<username>/krkn
#   KRKN_REPO_PATH=~/krkn

# Start Neo4j (optional, for Graphiti memory)
podman run -d --name neo4j-coordinator \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5-community

# Ingest knowledge base (one-time, ~6 min)
PYTHONPATH=. python -m src.knowledge.ingest ./chroma_data
```

### Environment Variables

Edit `.env` with your values. Required fields:

| Variable | Description | Default |
|----------|-------------|---------|
| `JIRA_URL` | JIRA instance URL | `https://redhat.atlassian.net` |
| `JIRA_USERNAME` | JIRA username / email | — |
| `JIRA_API_TOKEN` | JIRA API token | — |
| `GITHUB_TOKEN` | GitHub personal access token | — |
| `KRKN_REPO_URL` | Upstream krkn GitHub URL | `https://github.com/krkn-chaos/krkn` |
| `KRKN_REPO_PATH` | Path to your local krkn clone | `~/krkn` |
| `NEO4J_URI` | Neo4j connection URI | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password | — |
| `OCP_RELEASE` | Target OpenShift release | `4.21` |

> **Why is a local krkn clone required?**
> The MAP phase calls `index_scenarios_from_repo()` which walks `$KRKN_REPO_PATH/scenarios/` on disk to build a catalog of existing chaos scenarios. This catalog is loaded into ChromaDB so the pipeline can determine whether a scenario already exists before flagging a coverage gap. Without the clone, scenario indexing returns empty and everything appears as a gap.

### Run

Before running `./setup.sh`, you need:

| Requirement | How to install |
|-------------|---------------|
| Python 3.11+ | `brew install python@3.11` (macOS) or `sudo dnf install python3.11` (RHEL) |
| Podman or Docker | `brew install podman` (macOS) or `sudo dnf install podman` (RHEL) |
| Git | Usually pre-installed |
| Claude Code CLI (optional) | [claude.ai/download](https://claude.ai/download) — needed only for `claude_code` LLM provider |

### Getting API Tokens

The setup script will prompt you for these, but you can generate them in advance:

**JIRA API Token:**
1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**, name it anything
3. Your username is your Red Hat email (e.g., `you@redhat.com`)

**GitHub Personal Access Token:**
1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Click **Generate new token (classic)**, select `repo` scope

### Environment Variables

All configuration lives in `.env` (created by setup script). Full reference:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JIRA_URL` | No | `https://redhat.atlassian.net` | JIRA instance URL |
| `JIRA_USERNAME` | Yes | — | Your JIRA email |
| `JIRA_API_TOKEN` | Yes | — | JIRA API token |
| `GITHUB_TOKEN` | Yes | — | GitHub PAT |
| `NEO4J_PASSWORD` | Yes | `password` | Neo4j password |
| `NEO4J_URI` | No | `bolt://localhost:7687` | Neo4j connection URI |
| `LLM_PROVIDER` | No | auto-detected | `claude_code`, `anthropic`, `ollama`, `openai`, `google`, or `none` |
| `LLM_MODEL` | No | `claude-sonnet-4-6` | Model name for LLM calls |
| `KRKN_REPO_PATH` | No | `~/krkn` | Path to local krkn repo clone |
| `OCP_RELEASE` | No | `4.21` | Target OpenShift release |

---

## Running

### Option 1: Claude Code (recommended)

```bash
cd ~/krkn-chaos-coordinator
claude
# Then type: /run-scan
# Interactive: asks for OCP version + agent selection
```

### Option 2: CLI

```bash
# Single agent, single version
PYTHONPATH=. python src/main.py --release 4.21 --agent control_plane --use-llm

# Multiple agents
PYTHONPATH=. python src/main.py --release 4.21 --agent control_plane,networking --use-llm

# Multiple versions
PYTHONPATH=. python src/main.py --release 4.20,4.21 --use-llm

# All agents (production run)
PYTHONPATH=. python src/main.py --release 4.21 --use-llm

# Keyword filter only (no LLM, fast)
PYTHONPATH=. python src/main.py --release 4.21

# Custom lookback window
PYTHONPATH=. python src/main.py --release 4.21 --use-llm --days 30
```

### Option 3: Streamlit Dashboard

```bash
PYTHONPATH=. streamlit run src/ui/web_dashboard.py --server.port 8501
```

---

## Adding a New Agent

Create a single YAML file in `config/agents/`. No code changes needed.

```yaml
# config/agents/virtualization.yaml
name: virtualization
description: "OpenShift Virtualization / CNV / KubeVirt"

# JIRA components this agent monitors
components:
  - "OpenShift Virtualization"
  - "Virtualization / virt-controller"
  - "Virtualization / virt-handler"

# Domain-specific filter keywords (merged with common keywords from config/filters/common.yaml)
filter:
  chaos_keywords:
    - "vm migration failed"
    - "virt-launcher crash"
    - "live migrate timeout"
  skip_keywords:
    - "cnv-must-gather"

# Domain-specific docs for ChromaDB (improves LLM reasoning for this domain)
docs:
  - type: github
    owner: kubevirt
    repo: kubevirt
    path: docs
  - type: local
    path: ~/my-cnv-docs
  - type: url
    url: https://kubevirt.io/user-guide/architecture/
```

Then:
```bash
# Ingest docs (if you added a docs section)
PYTHONPATH=. python -m src.knowledge.ingest ./chroma_data

# Run the agent
PYTHONPATH=. python src/main.py --release 4.21 --agent virtualization --use-llm
```

See [config/agents/README.md](config/agents/README.md) for full reference.

### Customizing Filter Keywords

Common keywords shared across all agents live in `config/filters/common.yaml`. Agent-specific keywords are added via the `filter` section in each agent's YAML and merged on top at runtime.

See [config/filters/README.md](config/filters/README.md) for details.

---

## Run Tests

```bash
# Unit tests (no external deps, ~0.2s)
PYTHONPATH=. pytest tests/unit/ -v                    # 187 tests

# Integration tests (requires Neo4j)
PYTHONPATH=. pytest tests/integration/ -v             # 13 tests

# All tests
PYTHONPATH=. pytest tests/ -v                         # 200 total

# Run filter eval (Sonnet vs Haiku comparison)
PYTHONPATH=. python -m src.evals.filter_eval --sample-size 20
```

## Project Structure

```
config/
├── agents/                        # Drop a YAML file here to add a new agent
│   ├── control_plane.yaml         # 6 built-in agents (name, components, filter, docs)
│   ├── networking.yaml
│   ├── node_machine.yaml
│   ├── storage.yaml
│   ├── operators_platform.yaml
│   └── upgrade_lifecycle.yaml
└── filters/
    └── common.yaml                # Shared filter keywords (skip + chaos)

src/
├── main.py                        # CLI entry point (multi-version, multi-agent)
├── models.py                      # Domain models (Bug, Gap, Observation, RunMetrics)
├── reasoning.py                   # LLM reasoning for MAP + ANALYZE phases
├── logging_util.py                # Structured JSON logging
├── coordinator/
│   └── orchestrator.py            # Dedup, approval queue, run summary
├── agents/
│   ├── base_agent.py              # Pipeline: DISCOVER→FILTER→MAP→ANALYZE→ACT→REMEMBER
│   ├── registry.py                # Auto-discovers agents from config/agents/*.yaml
│   ├── pr_creator.py              # Draft PR creation
│   ├── hub_generator.py           # krkn-hub boilerplate
│   └── docs_generator.py          # Website docs
├── apis/
│   ├── jira_client.py             # JIRA REST API (4-tier version query)
│   ├── sippy_client.py            # Sippy regressions + health
│   ├── github_client.py           # GitHub API
│   └── release_client.py          # Z-stream changelog enrichment
├── knowledge/
│   ├── chromadb_store.py          # Vector search (4 collections)
│   ├── neo4j_store.py             # Graph memory (single backend)
│   ├── component_map.py           # Delegates to registry for agent → component mapping
│   ├── ingest.py                  # Doc ingestion (GitHub, local, URL + agent-specific)
│   ├── filter_cache.py            # Semantic cache (Cache-Aside pattern)
│   ├── scenario_index.py          # Index krkn scenario YAMLs
│   └── scenario_knowledgebase.py  # krkn-knowledgebase integration
├── filter/
│   ├── chaos_filter.py            # Keyword filter (loads from config/filters/ + agent YAML)
│   ├── llm_filter.py              # LLM filter (5 providers, token tracking)
│   ├── llm_config.py              # Per-phase model routing + auto-detection
│   ├── llm_tools.py               # Typed tool functions with Observation returns
│   └── llm_batch.py               # Anthropic Batch API support
├── evals/
│   ├── filter_eval.py             # Model comparison eval
│   ├── sampler.py                 # Stratified bug sampler
│   └── eval_report.py             # Eval metrics + pass criteria
└── ui/
    ├── terminal_ui.py             # Rich terminal dashboard
    └── web_dashboard.py           # Streamlit web dashboard
```

## License

Apache-2.0
