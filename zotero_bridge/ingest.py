"""Example ingestion workflow using the ZoteroBridge SDK.

Workflow:
    1. Agent finds an interesting paper.
    2. Duplication check: Query the Zotero library (by DOI / arXiv ID / URL / title).
    3. If missing:
        - Call the magic wand to fetch high-quality metadata + PDF.
        - Create the venue-named collection if it doesn't exist.
        - Place the item into that venue collection.
    4. Regardless of whether it was newly created or pre-existing:
        - Add to the project-specific collection.

Usage::

    python -m zotero_bridge.ingest \
        --doi "10.1145/3597926.3598095" \
        --venue "ASPLOS 2024" \
        --project "MyResearch"
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from .client import ZoteroBridge, ZoteroBridgeError
from .dblp import normalize_venue_name


def ingest(
    bridge: ZoteroBridge,
    identifier: str,
    id_type: str = "DOI",
    venue: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Run the full ingestion pipeline.

    Venue names are normalised to DBLP convention (e.g. ``issta2023``,
    ``asplos2025-1``).  If ``venue`` is not supplied explicitly, the
    name is derived automatically from the item's metadata.

    Returns a dict describing what happened (created, updated, collections, etc.).
    """
    result: dict[str, Any] = {"identifier": identifier, "id_type": id_type}

    # 1. Duplication check
    dup = bridge.check_duplicate(identifier, id_type)
    result["duplicate_check"] = dup

    item_id: int | None = None
    item_key: str | None = None

    if dup.get("found"):
        item_id = dup["itemID"]
        item_key = dup.get("key")
        result["action"] = "existing"
        print(f"[ingest] Item already exists (ID={item_id}, key={item_key})")
    else:
        # 2. Fetch metadata
        print(f"[ingest] Identifier not found — fetching metadata for {identifier} ...")
        added = bridge.add_by_identifier(identifier, id_type)
        result["add_result"] = added

        if added.get("status") != "success":
            result["action"] = "failed"
            print(f"[ingest] Failed to add item: {added}", file=sys.stderr)
            return result

        item_id = added["itemID"]
        item_key = added.get("key")
        result["action"] = "created"
        print(f"[ingest] Created item (ID={item_id}, key={item_key})")


    # 3. Fetch PDF when missing. This also covers URL-identified papers that
    # already existed in Zotero but had only a webpage/biburl attachment.
    existing_pdf = bridge.retrieve_pdf(item_id)
    result["existing_pdf"] = existing_pdf
    if existing_pdf:
        print(f"[ingest] PDF already attached (attachmentID={existing_pdf.get('attachmentID')})")
    else:
        print(f"[ingest] Attempting to retrieve PDF ...")
        ft = bridge.find_fulltext(item_id)
        result["fulltext_result"] = ft
        if ft.get("status") == "success":
            print(f"[ingest] PDF attached (attachmentID={ft.get('attachmentID')})")
        else:
            print(f"[ingest] No PDF found automatically")

    # 4. Resolve venue name (DBLP-style) -----------------------------
    if not venue:
        item_meta = bridge.get_item(item_id)
        venue = normalize_venue_name(item_meta)
        print(f"[ingest] Auto-derived venue name: '{venue}'")
    else:
        # Even when the user passed a raw venue name, normalise it.
        # We build a minimal pseudo-item so the normaliser has something to work with.
        pseudo_item: dict[str, Any] = {"conferenceName": venue}
        if item_id:
            item_meta = bridge.get_item(item_id)
            if item_meta:
                pseudo_item["date"] = item_meta.get("date")
                pseudo_item["volume"] = item_meta.get("volume")
        venue = normalize_venue_name(pseudo_item)
        print(f"[ingest] Normalised venue name: '{venue}'")

    result["venue_name"] = venue
    venue_col = bridge.get_or_create_collection(venue)
    result["venue_collection"] = venue_col
    bridge.add_to_collection(item_id, venue_col["id"])
    print(f"[ingest] Added to venue collection '{venue}' (ID={venue_col['id']})")

    # 5. Alias into project collection (always)
    if project:
        proj_col = bridge.get_or_create_collection(project)
        result["project_collection"] = proj_col
        bridge.add_to_collection(item_id, proj_col["id"])
        print(f"[ingest] Added to project collection '{project}' (ID={proj_col['id']})")

    result["item_id"] = item_id
    result["item_key"] = item_key
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a paper into Zotero.")
    parser.add_argument("--doi", help="DOI of the paper")
    parser.add_argument("--arxiv", help="arXiv ID of the paper")
    parser.add_argument("--isbn", help="ISBN of the paper/book")
    parser.add_argument("--paper-url", help="Canonical paper URL")
    parser.add_argument("--title", help="Title to search (fallback)")
    parser.add_argument("--venue", help="Venue/collection name (optional; auto-derived from metadata if omitted)")
    parser.add_argument("--project", required=True, help="Project collection name")
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

    # Determine identifier & type
    if args.doi:
        identifier, id_type = args.doi, "DOI"
    elif args.arxiv:
        identifier, id_type = args.arxiv, "arXiv"
    elif args.isbn:
        identifier, id_type = args.isbn, "ISBN"
    elif args.paper_url:
        identifier, id_type = args.paper_url, "url"
    elif args.title:
        identifier, id_type = args.title, "title"
    else:
        parser.error("Provide one of --doi, --arxiv, --isbn, --paper-url, or --title")

    bridge = ZoteroBridge(base_url=args.bridge_url or args.url, token=args.token)
    try:
        result = ingest(
            bridge,
            identifier=identifier,
            id_type=id_type,
            venue=args.venue,
            project=args.project,
        )
        if result.get("action") == "failed":
            return 1
        print("\n[ingest] Done.")
        return 0
    except ZoteroBridgeError as e:
        print(f"[ingest] Error: {e}", file=sys.stderr)
        if e.response_text:
            print(f"[ingest] Response: {e.response_text}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
