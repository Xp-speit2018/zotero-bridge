"""Small Python SDK for Zotero debug-bridge."""

from .client import ZoteroBridge, ZoteroBridgeError
from .export import Exporter

__version__ = "0.5.2"
__all__ = ["ZoteroBridge", "ZoteroBridgeError", "Exporter", "__version__"]
