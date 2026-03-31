"""Storage domain agent — covers CSI drivers, PVC, image registry."""

from src.agents.base_agent import BaseDomainAgent


class StorageAgent(BaseDomainAgent):
    def __init__(self, **kwargs):
        super().__init__(agent_name="storage", **kwargs)
