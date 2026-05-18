"""Small Python SDK for Zotero debug-bridge."""

from .client import ZoteroBridge, ZoteroBridgeError

__version__ = "0.1.0"
__all__ = ["ZoteroBridge", "ZoteroBridgeError", "__version__"]
