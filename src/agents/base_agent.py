"""Base domain agent with the DISCOVER → FILTER → MAP → ANALYZE → ACT → REMEMBER pipeline."""

from __future__ import annotations

import logging
from abc import ABC

from src.apis.jira_client import JiraClient
from src.apis.sippy_client import SippyClient
from src.apis.github_client import GitHubClient
from src.filter.chaos_filter import filter_bug, filter_bugs
from src.filter.llm_filter import llm_filter_bugs
from src.knowledge.chromadb_store import ChromaStore
from src.knowledge.component_map import get_components_for_agent
from src.knowledge.filter_cache import SemanticFilterCache
from src.knowledge.neo4j_store import Neo4jStore
from src.knowledge.scenario_index import ScenarioInfo
from src.models import (
    AgentResult,
    Bug,
    FilterResult,
    GapAnalysis,
    MatchResult,
    ScenarioMatch,
)

logger = logging.getLogger(__name__)


class BaseDomainAgent(ABC):
    """Base class for domain-specific chaos coverage agents."""

    def __init__(
        self,
        agent_name: str,
        jira: JiraClient,
        sippy: SippyClient,
        github: GitHubClient,
        chroma: ChromaStore,
        scenarios: list[ScenarioInfo],
        release: str,
        neo4j_store: Neo4jStore,
        use_llm: bool = False,
        max_bugs: int = 2000,
        days: int = 14,
    ):
        self.agent_name = agent_name
        self.jira = jira
        self.sippy = sippy
        self.github = github
        self.chroma = chroma
        self.scenarios = scenarios
        self.release = release
        self.neo4j = neo4j_store
        self.use_llm = use_llm
        self.max_bugs = max_bugs
        self.days = days
        self.components = get_components_for_agent(agent_name)

        # Semantic filter cache — gracefully degrade if unavailable
        try:
            self._filter_cache: SemanticFilterCache | None = SemanticFilterCache(
                self.chroma._client,
            )
        except Exception as e:
            logger.warning("Semantic filter cache unavailable: %s", e)
            self._filter_cache = None

    def run(self) -> AgentResult:
        """Execute the full pipeline: DISCOVER → FILTER → MAP → ANALYZE → ACT → REMEMBER."""
        import asyncio
        logger.info("=== %s agent starting ===", self.agent_name)

        # DISCOVER
        bugs = self._discover()
        logger.info("DISCOVER: found %d bugs", len(bugs))

        # Enrich with z-stream changelog data
        bugs = self._enrich_with_changelog(bugs)

        # Split into new vs known bugs
        known_keys = self._get_known_bugs()
        new_bugs = [b for b in bugs if b.key not in known_keys]
        known_bugs = [b for b in bugs if b.key in known_keys]

        if known_bugs:
            logger.info("DISCOVER: %d known bugs — updating status in Neo4j", len(known_bugs))
            self._update_known_bugs(known_bugs)

        if new_bugs:
            logger.info("DISCOVER: %d new bugs to analyze", len(new_bugs))

        # FILTER
        relevant, skipped = self._filter(new_bugs)
        logger.info("FILTER: %d relevant, %d skipped", len(relevant), len(skipped))

        # MAP
        matched, unmatched = self._map(relevant)
        logger.info("MAP: %d matched, %d unmatched", len(matched), len(unmatched))

        # ANALYZE
        gaps = self._analyze(unmatched)
        logger.info("ANALYZE: %d gaps identified", len(gaps))

        result = AgentResult(
            agent_name=self.agent_name,
            bugs_discovered=bugs,
            bugs_filtered_out=skipped,
            bugs_matched=matched,
            gaps=gaps,
        )

        # REMEMBER
        self._remember(result)

        logger.info("=== %s agent complete ===", self.agent_name)
        return result

    def _enrich_with_changelog(self, bugs: list[Bug]) -> list[Bug]:
        """Enrich bugs with z-stream fix info from release changelogs."""
        try:
            from src.apis.release_client import get_all_fixed_bugs
            fixed_map = get_all_fixed_bugs(self.release)
        except Exception as e:
            logger.warning("Changelog enrichment failed: %s", e)
            return bugs

        if not fixed_map:
            return bugs

        enriched = []
        fixed_count = 0
        for bug in bugs:
            bug_fix = fixed_map.get(bug.key)
            if bug_fix:
                bug = Bug(
                    key=bug.key,
                    summary=bug.summary,
                    description=bug.description,
                    component=bug.component,
                    priority=bug.priority,
                    status=bug.status,
                    created=bug.created,
                    url=bug.url,
                    all_components=bug.all_components,
                    fixed_in_release=bug_fix.fixed_in,
                    fix_commits=bug_fix.commits,
                    fix_image=bug_fix.image,
                )
                fixed_count += 1
            enriched.append(bug)

        if fixed_count:
            logger.info("DISCOVER: %d bugs already fixed in z-streams", fixed_count)
        return enriched

    def _update_known_bugs(self, known_bugs: list[Bug]) -> None:
        """Update status/priority for already-analyzed bugs. Closes gaps for resolved bugs."""
        self.neo4j.update_bug_statuses(known_bugs)

    def _get_known_bugs(self) -> set[str]:
        """Get already-analyzed bug keys from Neo4j."""
        return self.neo4j.get_analyzed_bug_keys_sync()

    def _remember(self, result: AgentResult) -> None:
        """Store results in Neo4j."""
        self.neo4j.remember_result_sync(result)
        logger.info("REMEMBER: stored in Neo4j")

    def _discover(self) -> list[Bug]:
        """DISCOVER: Query JIRA and Sippy for bugs and regressions."""
        return self.jira.get_bugs_by_components(
            self.components, days=self.days, max_results=self.max_bugs,
            release=self.release,
        )

    def _filter(self, bugs: list[Bug]) -> tuple[list[FilterResult], list[FilterResult]]:
        """FILTER: Determine chaos relevance of each bug."""
        if self.use_llm:
            logger.info("Using tiered filter: keyword → semantic cache → LLM")
            return self._tiered_filter(bugs)
        return filter_bugs(bugs)

    def _tiered_filter(
        self, bugs: list[Bug],
    ) -> tuple[list[FilterResult], list[FilterResult]]:
        """Three-tier filter: keyword → semantic cache → LLM."""
        relevant: list[FilterResult] = []
        skipped: list[FilterResult] = []
        needs_llm: list[Bug] = []

        # Layer 1: Keyword pre-filter with confidence scoring
        for bug in bugs:
            kw_result = filter_bug(bug)
            if kw_result.confidence > 0.8:
                # High confidence — trust keyword filter
                if kw_result.chaos_relevant:
                    relevant.append(kw_result)
                else:
                    skipped.append(kw_result)
            elif kw_result.confidence < 0.2:
                skipped.append(kw_result)
            else:
                needs_llm.append(bug)

        # Layer 2: Semantic cache
        still_needs_llm: list[Bug] = []
        for bug in needs_llm:
            cached = self._filter_cache.get(bug.summary) if self._filter_cache else None
            if cached is not None:
                result = FilterResult(
                    bug=bug,
                    chaos_relevant=cached.chaos_relevant,
                    failure_mode=cached.failure_mode,
                    injection_method=cached.injection_method,
                    confidence=0.9,
                )
                if result.chaos_relevant:
                    relevant.append(result)
                else:
                    skipped.append(result)
            else:
                still_needs_llm.append(bug)

        # Layer 3: LLM
        if still_needs_llm:
            llm_relevant, llm_skipped = self._llm_filter_with_docs(still_needs_llm)
            relevant.extend(llm_relevant)
            skipped.extend(llm_skipped)
            # Cache LLM results for future runs
            if self._filter_cache:
                for r in [*llm_relevant, *llm_skipped]:
                    self._filter_cache.put(r.bug.summary, r)

        logger.info(
            "FILTER tiers: %d keyword, %d cached, %d LLM",
            len(bugs) - len(needs_llm),
            len(needs_llm) - len(still_needs_llm),
            len(still_needs_llm),
        )
        return relevant, skipped

    def _llm_filter_with_docs(
        self, bugs: list[Bug],
    ) -> tuple[list[FilterResult], list[FilterResult]]:
        """LLM filter enriched with per-component OCP docs from ChromaDB."""
        from src.filter.llm_filter import llm_filter_bug
        from src.filter.llm_config import detect_llm_backend

        config = detect_llm_backend()
        relevant = []
        skipped = []

        for i, bug in enumerate(bugs):
            logger.info("LLM filtering %d/%d: %s", i + 1, len(bugs), bug.key)

            # Search ChromaDB for component architecture docs + krkn capabilities
            components = bug.all_components or (bug.component,)
            ocp_docs = self.chroma.search_per_component(
                components, bug.summary, collection="all", n_results=3,
            )
            krkn_docs = self.chroma.search_per_component(
                components, bug.summary, collection="krkn_docs", n_results=3,
            )

            result = llm_filter_bug(bug, config=config, ocp_docs=ocp_docs, krkn_docs=krkn_docs)

            if result.chaos_relevant:
                relevant.append(result)
                logger.info("  PASS: %s (%s)", result.failure_mode, result.injection_method)
            else:
                skipped.append(result)
                logger.info("  SKIP: %s", result.skip_reason)

        logger.info("LLM filter: %d relevant, %d skipped", len(relevant), len(skipped))
        return relevant, skipped

    def _map(self, relevant: list[FilterResult]) -> tuple[list[ScenarioMatch], list[ScenarioMatch]]:
        """MAP: Match bugs against existing krkn scenarios using ChromaDB + local index."""
        matched = []
        unmatched = []

        for filter_result in relevant:
            bug = filter_result.bug
            match = self._find_scenario_match(bug, filter_result)
            if match.match_result == MatchResult.FULL_MATCH:
                matched.append(match)
            else:
                unmatched.append(match)

        return matched, unmatched

    def _find_scenario_match(self, bug: Bug, filter_result: FilterResult) -> ScenarioMatch:
        """Search for existing krkn scenarios that match a bug.

        Uses ChromaDB for retrieval — searches per component separately for
        multi-component bugs so results aren't diluted. If use_llm is True,
        LLM reasons over the retrieved results.
        """
        summary_parts = [bug.summary]
        if filter_result.failure_mode:
            summary_parts.append(filter_result.failure_mode)
        if filter_result.injection_method:
            summary_parts.append(filter_result.injection_method)
        summary = " ".join(summary_parts)

        components = bug.all_components or (bug.component,)

        # ChromaDB retrieval — per component, merged
        scenario_hits = self.chroma.search_per_component(
            components, summary, collection="scenarios", n_results=5,
        )
        doc_hits = self.chroma.search_per_component(
            components, summary, collection="krkn_docs", n_results=5,
        )

        # Knowledge base: what krkn scenarios are POSSIBLE to build
        kb_context = None
        try:
            from src.knowledge.scenario_knowledgebase import ScenarioKnowledgeBase
            from src.generator.scenario_generator import match_scenario
            from src.models import GapAnalysis, Confidence, ActionType

            kb = ScenarioKnowledgeBase()
            # Create a temporary gap to reuse match_scenario logic
            temp_gap = GapAnalysis(
                bug=bug,
                reasoning=filter_result.injection_method or "",
                modifications=[filter_result.failure_mode or ""],
            )
            matched_kb = match_scenario(temp_gap, kb)
            if matched_kb:
                kb_context = {
                    "scenario_name": matched_kb.get("scenario_name"),
                    "title": matched_kb.get("title"),
                    "description": matched_kb.get("description", "")[:200],
                    "parameters": [
                        p.get("name") for p in matched_kb.get("parameters", [])
                    ],
                }
        except Exception as e:
            logger.debug("Knowledge base lookup in MAP: %s", e)

        if self.use_llm:
            from src.reasoning import llm_map_match
            return llm_map_match(bug, filter_result, scenario_hits, doc_hits, kb_context=kb_context)

        # Fallback: threshold-based matching
        from src.reasoning import _fallback_match
        return _fallback_match(bug, scenario_hits)

    def _analyze(self, unmatched: list[ScenarioMatch]) -> list[GapAnalysis]:
        """ANALYZE: Score confidence and determine action for each gap.

        If use_llm is True, LLM reasons over ChromaDB context + Neo4j history
        to produce specific recommendations. Otherwise, uses keyword scoring.
        """
        gaps = []

        for match in unmatched:
            bug = match.bug

            if self.use_llm:
                # Gather context for LLM — search per component
                components = bug.all_components or (bug.component,)
                ocp_docs = self.chroma.search_per_component(
                    components, bug.summary, collection="all", n_results=5,
                )
                krkn_docs = self.chroma.search_per_component(
                    components, bug.summary, collection="krkn_docs", n_results=3,
                )

                neo4j_history = []
                try:
                    for comp in components:
                        neo4j_history.extend(
                            self.neo4j.get_similar_resolved_bugs(comp)
                        )
                except Exception as e:
                    logger.warning("Neo4j history lookup failed: %s", e)

                from src.reasoning import llm_analyze_gap
                gap = llm_analyze_gap(
                    bug=bug,
                    match=match,
                    ocp_docs=ocp_docs,
                    krkn_docs=krkn_docs,
                    neo4j_history=neo4j_history,
                )
            else:
                from src.reasoning import _fallback_analyze
                gap = _fallback_analyze(bug, match)

            gaps.append(gap)

        return gaps
