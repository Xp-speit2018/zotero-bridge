"""Command-line collection export helpers."""

from __future__ import annotations

import argparse
import json
import sys

from .client import ZoteroBridge, ZoteroBridgeError


def _resolve_collection_id(bridge: ZoteroBridge, name: str | None, collection_id: int | None) -> int:
    if collection_id is not None:
        return collection_id
    if not name:
        raise ValueError("Provide --collection or --collection-id")

    matches = [c for c in bridge.get_collections() if c.get("name") == name]
    if not matches:
        raise ValueError(f"Collection not found: {name}")
    if len(matches) > 1:
        ids = ", ".join(str(c["id"]) for c in matches)
        raise ValueError(f"Collection name is ambiguous: {name} (IDs: {ids})")
    return int(matches[0]["id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a Zotero collection as an importable package.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--collection", help="Collection name to export")
    group.add_argument("--collection-id", type=int, help="Numeric Zotero collection ID to export")
    parser.add_argument("--output", required=True, help="Output directory, or zip path when --zip is set")
    parser.add_argument("--format", default="zotero-rdf", help="Primary export format (default: zotero-rdf)")
    parser.add_argument(
        "--extra-format",
        action="append",
        dest="extra_formats",
        help="Additional text export format to include. Repeatable. Defaults to better-bibtex and ris.",
    )
    parser.add_argument("--no-notes", action="store_true", help="Do not export Zotero child notes")
    parser.add_argument("--no-files", action="store_true", help="Do not copy attachment files")
    parser.add_argument("--zip", action="store_true", help="Also create a zip archive")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output directory or zip")
    parser.add_argument(
        "--url",
        default=None,
        help="Debug-bridge URL (deprecated alias for --bridge-url)",
    )
    parser.add_argument("--bridge-url", default=None, help="Debug-bridge URL")
    parser.add_argument("--token", default=None, help="Debug-bridge bearer token")
    args = parser.parse_args(argv)

    bridge = ZoteroBridge(base_url=args.bridge_url or args.url, token=args.token)
    try:
        collection_id = _resolve_collection_id(bridge, args.collection, args.collection_id)
        manifest = bridge.export.collection_package(
            collection_id,
            args.output,
            format=args.format,
            include_notes=not args.no_notes,
            include_files=not args.no_files,
            extra_formats=args.extra_formats,
            zip_output=args.zip,
            overwrite=args.overwrite,
        )
    except (ValueError, FileExistsError, ZoteroBridgeError) as e:
        print(f"[export] Error: {e}", file=sys.stderr)
        if isinstance(e, ZoteroBridgeError) and e.response_text:
            print(f"[export] Response: {e.response_text}", file=sys.stderr)
        return 1

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
