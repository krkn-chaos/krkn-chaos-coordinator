"""krkn-knowledgebase loader — structured JSON schemas for scenario generation.

Loads scenario definitions from the krkn-knowledgebase repo (cloned locally).
Each scenario has: parameters with types/defaults/validators, command templates
for krknctl and krkn-hub, config file mappings, and edge cases.

This is NOT ChromaDB — it's direct file loading for structured data that needs
to be read whole, not searched by semantic similarity.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KB_DIR = Path.home() / ".krkn" / "knowledgebase" / "knowledge-base"
KB_REPO = "https://github.com/ddjain/krkn-knowledgebase.git"


class ScenarioKnowledgeBase:
    """Load and query krkn scenario schemas from the knowledge base."""

    def __init__(self, kb_dir: str | Path = DEFAULT_KB_DIR):
        self.dir = Path(kb_dir)
        self._index: dict | None = None
        self._scenarios: dict[str, dict] = {}

    def sync(self) -> bool:
        """Clone or update the knowledge base repo."""
        repo_dir = self.dir.parent
        if (repo_dir / ".git").exists():
            try:
                subprocess.run(
                    ["git", "-C", str(repo_dir), "pull", "--quiet"],
                    capture_output=True, timeout=30,
                )
                logger.info("Knowledge base updated: %s", repo_dir)
                return True
            except Exception as e:
                logger.warning("Knowledge base pull failed: %s", e)
                return self.dir.exists()
        else:
            try:
                repo_dir.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "clone", KB_REPO, str(repo_dir)],
                    capture_output=True, timeout=60,
                )
                logger.info("Knowledge base cloned: %s", repo_dir)
                return True
            except Exception as e:
                logger.warning("Knowledge base clone failed: %s", e)
                return False

    @property
    def index(self) -> dict:
        """Load and cache index.json."""
        if self._index is None:
            index_path = self.dir / "index.json"
            if not index_path.exists():
                logger.error("Knowledge base not found at %s", index_path)
                return {"scenarios": []}
            with open(index_path) as f:
                self._index = json.load(f)
        return self._index

    def list_scenarios(self) -> list[dict]:
        """List all available scenario definitions."""
        return self.index.get("scenarios", [])

    def get_scenario(self, scenario_name: str) -> dict | None:
        """Load a complete scenario schema by name.

        Returns the full JSON with parameters, templates, validators, edge cases.
        """
        if scenario_name in self._scenarios:
            return self._scenarios[scenario_name]

        path = self.dir / "scenarios" / f"{scenario_name}.json"
        if not path.exists():
            logger.warning("Scenario not found: %s", path)
            return None

        with open(path) as f:
            schema = json.load(f)

        self._scenarios[scenario_name] = schema
        return schema

    def find_scenario(self, query: str) -> dict | None:
        """Find a scenario by plugin type, category, or name.

        Tries multiple matching strategies:
        1. Exact name match: "node-cpu-hog"
        2. scenario_type match: "hog_scenarios" → node-cpu-hog
        3. Category match: "network" → network-chaos
        4. Fuzzy name match: "cpu hog" → node-cpu-hog
        """
        query_lower = query.lower().strip()

        for entry in self.list_scenarios():
            name = entry.get("name", "")

            # Exact name match
            if name == query_lower:
                return self.get_scenario(name)

        # Load each scenario and check scenario_type
        for entry in self.list_scenarios():
            name = entry.get("name", "")
            schema = self.get_scenario(name)
            if schema and schema.get("scenario_type", "").lower() == query_lower:
                return schema

        # Category match
        for entry in self.list_scenarios():
            if entry.get("category", "").lower() == query_lower:
                return self.get_scenario(entry["name"])

        # Fuzzy: check if query words appear in scenario name or title
        query_words = set(query_lower.replace("_", "-").replace(" ", "-").split("-"))
        query_words.discard("")
        best_match = None
        best_score = 0

        for entry in self.list_scenarios():
            name = entry.get("name", "")
            title = entry.get("title", "").lower()
            name_words = set(name.split("-"))
            title_words = set(title.split())

            score = len(query_words & (name_words | title_words))
            if score > best_score:
                best_score = score
                best_match = name

        if best_match and best_score > 0:
            return self.get_scenario(best_match)

        return None

    def get_parameters(self, scenario: dict) -> list[dict]:
        """Get parameter definitions from a scenario schema."""
        return scenario.get("parameters", [])

    def get_parameter_by_name(self, scenario: dict, param_name: str) -> dict | None:
        """Look up a specific parameter definition."""
        for p in self.get_parameters(scenario):
            if p.get("name") == param_name:
                return p
        return None

    def validate_parameter(self, param_def: dict, value) -> str | None:
        """Validate a parameter value against its schema definition.

        Returns error message if invalid, None if valid.
        """
        name = param_def.get("name", "?")
        ptype = param_def.get("type", "string")

        # Type check
        if ptype == "number":
            try:
                value = float(value)
            except (ValueError, TypeError):
                return f"{name}: expected number, got {type(value).__name__}"

            validator = param_def.get("validator", {})
            if "min" in validator and value < validator["min"]:
                return f"{name}: {value} below minimum {validator['min']}"
            if "max" in validator and value > validator["max"]:
                return f"{name}: {value} above maximum {validator['max']}"

        elif ptype == "string":
            value = str(value)
            validator = param_def.get("validator", {})
            if "pattern" in validator:
                import re
                if not re.match(validator["pattern"], value):
                    msg = validator.get("message", f"does not match pattern {validator['pattern']}")
                    return f"{name}: '{value}' — {msg}"

        return None

    def generate_krknctl_command(self, scenario: dict, params: dict) -> str:
        """Generate a validated krknctl CLI command.

        Only includes non-default parameters to keep commands clean.
        """
        templates = scenario.get("command_templates", {})
        base = templates.get("krknctl", {}).get("base_command", "krknctl run unknown")

        parts = [base]
        for param_def in self.get_parameters(scenario):
            name = param_def.get("name")
            if name not in params:
                continue

            value = params[name]
            default = param_def.get("default")

            # Skip if value equals default
            if value == default:
                continue

            flag = param_def.get("maps_to", {}).get("krknctl")
            if flag:
                parts.append(f"{flag} {value}")

        return " \\\n  ".join(parts)

    def generate_krknhub_command(self, scenario: dict, params: dict) -> str:
        """Generate a validated krkn-hub Docker command."""
        image = scenario.get("container_image", "quay.io/krkn-chaos/krkn-hub:unknown")

        env_parts = []
        for param_def in self.get_parameters(scenario):
            name = param_def.get("name")
            if name not in params:
                continue

            value = params[name]
            env_var = param_def.get("maps_to", {}).get("krkn_hub")
            if env_var:
                env_parts.append(f"-e {env_var}={value}")

        env_str = " \\\n  ".join(env_parts)
        return (
            f"docker run --name krkn-{scenario.get('scenario_name', 'chaos')} \\\n"
            f"  {env_str} \\\n"
            f"  -v ~/.kube/config:/home/krkn/.kube/config:Z \\\n"
            f"  {image}"
        )

    def generate_scenario_yaml(self, scenario: dict, params: dict) -> str:
        """Generate scenario config YAML from the config_file_mapping."""
        cfm = scenario.get("config_file_mapping", {})
        structure = cfm.get("structure", {})
        fields = structure.get("fields", [])

        if not fields:
            return "# No config file mapping available for this scenario"

        yaml_lines = [f"# Generated from krkn-knowledgebase: {scenario.get('scenario_name', '?')}"]

        # Build config dict from field mappings
        config = {}
        for field in fields:
            config_field = field.get("config_field")
            maps_from = field.get("maps_from")
            if maps_from and maps_from in params:
                config[config_field] = params[maps_from]
            elif maps_from:
                # Use default from parameter definition
                param_def = self.get_parameter_by_name(scenario, maps_from)
                if param_def and param_def.get("default") is not None:
                    config[config_field] = param_def["default"]

        # Format as YAML
        fmt = structure.get("format", "unknown")
        scenario_type = scenario.get("scenario_type", "unknown")

        yaml_lines.append(f"{scenario_type}:")
        for key, value in config.items():
            if isinstance(value, str):
                yaml_lines.append(f"  {key}: \"{value}\"")
            else:
                yaml_lines.append(f"  {key}: {value}")

        return "\n".join(yaml_lines)

    def get_edge_cases(self, scenario: dict) -> list[str]:
        """Get edge cases and warnings for a scenario."""
        return scenario.get("edge_cases", []) + scenario.get("notes", [])
