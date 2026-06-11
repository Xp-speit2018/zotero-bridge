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
import re
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
        if id_type.lower() == "title":
            result["action"] = "failed"
            result["reason"] = "title_lookup_only"
            print(
                "[ingest] Title lookup found no existing item. "
                "Use --doi/--arxiv/--isbn/--paper-url for paper ingest, "
                "or --webpage-url with --title for documentation/webpage items.",
                file=sys.stderr,
            )
            return result

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


def _looks_like_pdf_url(url: str) -> bool:
    return bool(re.search(r"\.pdf(?:$|[?#])", url, flags=re.IGNORECASE))


def ingest_webpage(
    bridge: ZoteroBridge,
    url: str,
    *,
    title: str,
    project: str | None = None,
    author: str | None = None,
    date: str | None = None,
    abstract: str | None = None,
    tags: list[str] | None = None,
    attach_url: str | None = None,
) -> dict[str, Any]:
    """Create or reuse a Zotero ``webpage`` item from explicit metadata.

    This path is intentionally separate from ``ingest()``. ``ingest()`` is for
    scholarly identifiers and paper pages that Zotero's translators can resolve.
    ``ingest_webpage()`` is for documentation pages, project websites, and
    direct PDF URLs where magic-wand identifier lookup is the wrong first step.
    """
    result: dict[str, Any] = {"url": url, "item_type": "webpage"}

    dup = bridge.check_duplicate(url, "url")
    result["duplicate_check"] = dup

    project_collection: dict[str, Any] | None = None
    collection_ids: list[int] = []
    if project:
        project_collection = bridge.get_or_create_collection(project)
        result["project_collection"] = project_collection
        collection_ids.append(project_collection["id"])

    if dup.get("found"):
        item_id = dup["itemID"]
        item_key = dup.get("key")
        result["action"] = "existing"
        print(f"[ingest] Webpage already exists (ID={item_id}, key={item_key})")
        if project_collection:
            bridge.add_to_collection(item_id, project_collection["id"])
            print(f"[ingest] Added to project collection '{project}' (ID={project_collection['id']})")
    else:
        fields = {"title": title, "url": url}
        if date:
            fields["date"] = date
        if abstract:
            fields["abstractNote"] = abstract
        creators = [{"name": author, "creatorType": "author"}] if author else []
        created = bridge.create_item(
            item_type="webpage",
            fields=fields,
            creators=creators,
            tags=tags or [],
            collection_ids=collection_ids,
        )
        result["create_result"] = created
        if created.get("status") != "success":
            result["action"] = "failed"
            print(f"[ingest] Failed to create webpage item: {created}", file=sys.stderr)
            return result
        item_id = created["itemID"]
        item_key = created.get("key")
        result["action"] = "created"
        print(f"[ingest] Created webpage item (ID={item_id}, key={item_key})")

    existing_pdf = bridge.retrieve_pdf(item_id)
    result["existing_pdf"] = existing_pdf
    if existing_pdf:
        print(f"[ingest] PDF already attached (attachmentID={existing_pdf.get('attachmentID')})")
    else:
        effective_attach_url = attach_url or (url if _looks_like_pdf_url(url) else None)
        if effective_attach_url:
            print(f"[ingest] Attaching file from URL ...")
            attached = bridge.attach_file_from_url(item_id, effective_attach_url)
            result["attachment_result"] = attached
            if attached.get("status") == "success":
                print(f"[ingest] File attached (attachmentID={attached.get('attachmentID')})")
            else:
                print(f"[ingest] File attachment failed: {attached}", file=sys.stderr)
        else:
            print("[ingest] No attachment URL supplied; skipping full-text lookup for webpage item")

    result["item_id"] = item_id
    result["item_key"] = item_key
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a paper into Zotero.")
    parser.add_argument("--doi", help="DOI of the paper")
    parser.add_argument("--arxiv", help="arXiv ID of the paper")
    parser.add_argument("--isbn", help="ISBN of the paper/book")
    parser.add_argument("--paper-url", help="Canonical paper URL")
    parser.add_argument(
        "--webpage-url",
        help="Documentation/project/webpage URL to create as a Zotero webpage item; bypasses magic-wand identifier lookup",
    )
    parser.add_argument("--title", help="Title to search (fallback)")
    parser.add_argument("--author", help="Single corporate/person author for --webpage-url items")
    parser.add_argument("--date", help="Date/year for --webpage-url items")
    parser.add_argument("--abstract", help="Abstract/summary for --webpage-url items")
    parser.add_argument("--tag", action="append", dest="tags", help="Tag for --webpage-url items. Repeatable.")
    parser.add_argument("--attach-url", help="Direct attachment URL for --webpage-url items, e.g. a PDF")
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

    bridge = ZoteroBridge(base_url=args.bridge_url or args.url, token=args.token)
    try:
        if args.webpage_url:
            if not args.title:
                parser.error("--webpage-url requires --title")
            result = ingest_webpage(
                bridge,
                args.webpage_url,
                title=args.title,
                project=args.project,
                author=args.author,
                date=args.date,
                abstract=args.abstract,
                tags=args.tags,
                attach_url=args.attach_url,
            )
            if result.get("action") == "failed":
                return 1
            print("\n[ingest] Done.")
            return 0

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
            parser.error("Provide one of --doi, --arxiv, --isbn, --paper-url, --webpage-url, or --title")

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
