"""Networking domain agent — covers OVN-K, ingress, DNS, SDN."""

from src.agents.base_agent import BaseDomainAgent


class NetworkingAgent(BaseDomainAgent):
    def __init__(self, **kwargs):
        super().__init__(agent_name="networking", **kwargs)
