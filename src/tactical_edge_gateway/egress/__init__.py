"""Tactical C2 network egress clients for Cursor on Target and TAK Server."""

from tactical_edge_gateway.egress.base import BaseEgressClient, EgressStats
from tactical_edge_gateway.egress.tcp import TcpEgressClient
from tactical_edge_gateway.egress.udp import UdpEgressClient

__all__ = [
    "BaseEgressClient",
    "EgressStats",
    "UdpEgressClient",
    "TcpEgressClient",
]
