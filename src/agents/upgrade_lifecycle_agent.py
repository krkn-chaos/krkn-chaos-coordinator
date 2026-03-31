"""Upgrade & Lifecycle domain agent — covers CVO, MCO, Installer."""

from src.agents.base_agent import BaseDomainAgent


class UpgradeLifecycleAgent(BaseDomainAgent):
    def __init__(self, **kwargs):
        super().__init__(agent_name="upgrade_lifecycle", **kwargs)
