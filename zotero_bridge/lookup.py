"""Command-line lookup for existing Zotero items."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .client import ZoteroBridge, ZoteroBridgeError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Look up existing Zotero items and print JSON.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--doi", help="DOI to look up")
    group.add_argument("--arxiv", help="arXiv ID to look up")
    group.add_argument("--isbn", help="ISBN to look up")
    group.add_argument("--title", help="Title text to search")
    group.add_argument("--paper-url", help="Canonical paper URL to look up")
    parser.add_argument("--notes", action="store_true", help="Include child note metadata")
    parser.add_argument("--attachments", action="store_true", help="Include attachment/PDF metadata")
    parser.add_argument("--first", action="store_true", help="Return only the first matching item")
    parser.add_argument(
        "--url",
        default=os.getenv("ZOTERO_BRIDGE_URL", "http://localhost:23120"),
        help="Debug-bridge URL (deprecated alias for --bridge-url)",
    )
    parser.add_argument(
        "--bridge-url",
        default=None,
        help="Debug-bridge URL (default: $ZOTERO_BRIDGE_URL or http://localhost:23120)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ZOTERO_BRIDGE_TOKEN", "zotero-debug"),
        help="Debug-bridge token (default: $ZOTERO_BRIDGE_TOKEN or 'zotero-debug')",
    )
    args = parser.parse_args(argv)

    if args.doi:
        identifier, id_type = args.doi, "DOI"
    elif args.arxiv:
        identifier, id_type = args.arxiv, "arXiv"
    elif args.isbn:
        identifier, id_type = args.isbn, "ISBN"
    elif args.paper_url:
        identifier, id_type = args.paper_url, "url"
    else:
        identifier, id_type = args.title, "title"

    bridge = ZoteroBridge(base_url=args.bridge_url or args.url, token=args.token)
    try:
        result = bridge.lookup(
            identifier,
            id_type,
            include_notes=args.notes,
            include_attachments=args.attachments,
            first_only=args.first,
        )
    except ZoteroBridgeError as e:
        print(f"[lookup] Error: {e}", file=sys.stderr)
        if e.response_text:
            print(f"[lookup] Response: {e.response_text}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
