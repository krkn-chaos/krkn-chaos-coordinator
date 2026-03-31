"""Operators & Platform domain agent — covers OLM, Console, Auth, Monitoring."""

from src.agents.base_agent import BaseDomainAgent


class OperatorsPlatformAgent(BaseDomainAgent):
    def __init__(self, **kwargs):
        super().__init__(agent_name="operators_platform", **kwargs)
