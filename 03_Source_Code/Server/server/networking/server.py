"""Compatibility import for the TCP server implementation."""
from server.network import ClientConnection, ServerThread, SynCanvasServer, lan_ip_addresses

__all__ = ["SynCanvasServer", "ServerThread", "ClientConnection", "lan_ip_addresses"]
