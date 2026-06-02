#!/usr/bin/env bash
set -euo pipefail

# krkn-chaos-coordinator setup script
# Usage: ./setup.sh

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo ""
echo "============================================"
echo "  krkn-chaos-coordinator — Setup"
echo "============================================"
echo ""

# ── Step 1: Check Python ──────────────────────────────────────

info "Checking Python version..."

PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$("$candidate" -c "import sys; print(sys.version_info.major)")
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)")
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$candidate"
            ok "Found $candidate ($ver)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.11+ is required. Install it with: brew install python@3.11"
fi

# ── Step 2: Create virtual environment ────────────────────────

info "Creating virtual environment..."

if [ -d "venv" ]; then
    warn "venv/ already exists — skipping creation"
else
    "$PYTHON" -m venv venv
    ok "Created venv/ with $PYTHON"
fi

source venv/bin/activate
ok "Activated virtual environment"

# ── Step 3: Install dependencies ──────────────────────────────

info "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"
ok "Installed project + dev dependencies"

# ── Step 4: Clone krkn repo ──────────────────────────────────

KRKN_PATH="${KRKN_REPO_PATH:-$HOME/krkn}"

info "Checking krkn repo at $KRKN_PATH..."

if [ -d "$KRKN_PATH/scenarios" ]; then
    ok "krkn repo found at $KRKN_PATH"
else
    info "Cloning krkn repo..."
    git clone --quiet https://github.com/krkn-chaos/krkn.git "$KRKN_PATH"
    ok "Cloned krkn to $KRKN_PATH"
fi

# ── Step 5: Setup .env ────────────────────────────────────────

info "Checking .env file..."

if [ -f ".env" ]; then
    warn ".env already exists — skipping creation"
else
    cp .env.example .env
    ok "Created .env from .env.example"
    echo ""
    warn "You need to edit .env with your credentials:"
    echo "  JIRA_USERNAME=your-email@redhat.com"
    echo "  JIRA_API_TOKEN=<from https://id.atlassian.com/manage-profile/security/api-tokens>"
    echo "  GITHUB_TOKEN=<from https://github.com/settings/tokens>"
    echo "  NEO4J_PASSWORD=password"
    echo ""
fi

# ── Step 6: Start Neo4j ──────────────────────────────────────

info "Checking Neo4j..."

CONTAINER_ENGINE=""
if command -v podman &>/dev/null; then
    CONTAINER_ENGINE="podman"
elif command -v docker &>/dev/null; then
    CONTAINER_ENGINE="docker"
fi

if [ -z "$CONTAINER_ENGINE" ]; then
    warn "Neither podman nor docker found — skipping Neo4j setup"
    warn "Install podman: brew install podman"
else
    # Check if container exists (running or stopped)
    if $CONTAINER_ENGINE ps --format '{{.Names}}' 2>/dev/null | grep -q "^neo4j-coordinator$"; then
        ok "Neo4j container is running"
    elif $CONTAINER_ENGINE ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^neo4j-coordinator$"; then
        info "Starting existing Neo4j container..."
        $CONTAINER_ENGINE start neo4j-coordinator
        ok "Neo4j started"
    else
        info "Creating Neo4j container..."
        $CONTAINER_ENGINE run -d --name neo4j-coordinator \
            -p 7474:7474 -p 7687:7687 \
            -e NEO4J_AUTH=neo4j/password \
            neo4j:5-community
        ok "Neo4j created and started (password: 'password')"
    fi

    # Wait for Neo4j to be ready
    info "Waiting for Neo4j to be ready..."
    for i in $(seq 1 15); do
        if curl -s -o /dev/null -w "%{http_code}" http://localhost:7474 2>/dev/null | grep -q "200"; then
            ok "Neo4j is ready at http://localhost:7474"
            break
        fi
        if [ "$i" -eq 15 ]; then
            warn "Neo4j not responding yet — it may still be starting up"
        fi
        sleep 2
    done
fi

# ── Step 7: Verify connections ────────────────────────────────

echo ""
info "Verifying setup..."

# Check .env has required values
ENV_OK=true
if grep -q "your-jira-api-token" .env 2>/dev/null; then
    warn "JIRA_API_TOKEN not set in .env"
    ENV_OK=false
fi
if grep -q "your-github-pat" .env 2>/dev/null || grep -q "your-github-token" .env 2>/dev/null; then
    warn "GITHUB_TOKEN not set in .env"
    ENV_OK=false
fi

if [ "$ENV_OK" = true ]; then
    # Test JIRA
    PYTHONPATH=. python -c "
from dotenv import load_dotenv; load_dotenv()
import os
from src.apis.jira_client import JiraClient, JiraConfig
try:
    jira = JiraClient(JiraConfig(url=os.environ['JIRA_URL'], username=os.environ['JIRA_USERNAME'], api_token=os.environ['JIRA_API_TOKEN']))
    bugs = jira.get_bugs_by_components(['Etcd'], days=7, max_results=3, release='4.21')
    print(f'JIRA: OK ({len(bugs)} bugs found)')
except Exception as e:
    print(f'JIRA: FAILED — {e}')
" 2>/dev/null || true

    # Test Neo4j
    PYTHONPATH=. python -c "
from dotenv import load_dotenv; load_dotenv()
import os
from src.knowledge.neo4j_store import Neo4jStore
try:
    store = Neo4jStore(password=os.environ.get('NEO4J_PASSWORD', 'password'))
    connected = store.connect()
    keys = store.get_analyzed_bug_keys() if connected else set()
    print(f'Neo4j: OK ({len(keys)} bugs in graph)' if connected else 'Neo4j: FAILED to connect')
    store.close()
except Exception as e:
    print(f'Neo4j: FAILED — {e}')
" 2>/dev/null || true
fi

# Test agent registry
PYTHONPATH=. python -c "
from src.agents.registry import discover_agents
agents = discover_agents()
names = ', '.join(sorted(agents.keys()))
print(f'Agents: {len(agents)} discovered ({names})')
" 2>/dev/null || true

# Run tests
info "Running tests..."
PYTHONPATH=. python -m pytest tests/unit/ -q --tb=no 2>/dev/null | tail -1 || true

# ── Done ──────────────────────────────────────────────────────

echo ""
echo "============================================"
echo -e "  ${GREEN}Setup complete!${NC}"
echo "============================================"
echo ""
echo "Next steps:"
echo ""
if [ "$ENV_OK" = false ]; then
    echo "  1. Edit .env with your JIRA and GitHub credentials"
    echo "  2. Run: source venv/bin/activate"
    echo "  3. Run: PYTHONPATH=. python -m src.knowledge.ingest ./chroma_data"
    echo "  4. Run: PYTHONPATH=. python src/main.py --release 4.21 --use-llm"
else
    echo "  1. Run: source venv/bin/activate"
    echo "  2. Run: PYTHONPATH=. python -m src.knowledge.ingest ./chroma_data  (one-time, ~6 min)"
    echo "  3. Run: PYTHONPATH=. python src/main.py --release 4.21 --use-llm"
fi
echo ""
echo "  Or use Claude Code:  claude → /run-scan"
echo ""
