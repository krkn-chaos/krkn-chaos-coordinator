"""Node & Machine domain agent — covers kubelet, MCO, Machine API."""

from src.agents.base_agent import BaseDomainAgent


class NodeMachineAgent(BaseDomainAgent):
    def __init__(self, **kwargs):
        super().__init__(agent_name="node_machine", **kwargs)
