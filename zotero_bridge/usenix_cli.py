"""Serial USENIX catalogue, local download and optional Zotero ingestion CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .client import ZoteroBridge, ZoteroBridgeError
from .usenix import METADATA_ENGINE, USENIX_TRANSLATOR_ID, NotUsenixPaper, UsenixClient, UsenixError, UsenixPaper, conference_id, pdf_integrity


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _verified_download(download: dict[str, Any], output: Path, event: str) -> dict[str, Any] | None:
    """Reuse a recorded file after checking its destination and digest."""
    path = Path(download.get("path", ""))
    if path.parent != output.expanduser().resolve() / event:
        return None
    try:
        info = pdf_integrity(path)
    except (OSError, UsenixError):
        return None
    if info["sha256"] != download.get("sha256"):
        return None
    return {**download, **info, "status": "existing"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "download", "ingest"])
    parser.add_argument("--event", action="append", help="Event ID, e.g. osdi25; repeat for multiple events")
    parser.add_argument("--conference", action="append", help="Acronym, e.g. OSDI; pair with --year")
    parser.add_argument("--year", type=int, action="append", help="Year; repeat to collect several years")
    parser.add_argument("--output", type=Path, default=Path("usenix-downloads"))
    parser.add_argument("--state", type=Path, help="Persistent manifest (default: <output>/state.json)")
    parser.add_argument("--limit", type=int, help="Maximum papers to attempt; completed downloads do not consume the limit")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch catalogues and metadata instead of using cached records")
    parser.add_argument("--interval", type=float, default=10, help="Minimum HTTP request interval; at least 10 seconds")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--project", help="Optional Zotero project collection")
    parser.add_argument("--bridge-url", help="Zotero debug-bridge URL; otherwise use SDK environment/defaults")
    parser.add_argument("--run", action="store_true", help="Perform Zotero writes for ingest (otherwise preview only)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if bool(args.conference) != bool(args.year):
        parser.error("--conference and --year must be supplied together")
    if not args.event and not args.conference:
        parser.error("Supply --event or --conference with --year")
    try:
        events = list(dict.fromkeys([conference_id(event) for event in args.event or []] +
                                   [conference_id(venue, year) for venue in args.conference or [] for year in args.year or []]))
        client = UsenixClient(interval=args.interval, timeout=args.timeout, max_attempts=args.max_attempts)
    except ValueError as error:
        parser.error(str(error))
    state_path = (args.state or args.output / "state.json").expanduser().resolve()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
            "version": 1, "events": {}, "papers": {}}
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("events"), dict) or not isinstance(state.get("papers"), dict):
            raise ValueError("Invalid USENIX manifest; expected version 1 with events and papers")
    except (OSError, ValueError) as error:
        print(f"[usenix] Cannot read manifest: {error}", file=sys.stderr)
        client.close()
        return 1
    persist = args.command == "download" or (args.command == "ingest" and args.run)
    bridge = ZoteroBridge(base_url=args.bridge_url, request_timeout=args.timeout) if persist else None
    client.bridge = bridge
    summary: Counter[str] = Counter()
    processed = 0
    listed: list[dict[str, str]] = []
    event_errors: list[dict[str, str]] = []
    planned: list[tuple[str, dict[str, str]]] = []
    try:
        with client:
            for event in events:
                try:
                    if args.refresh or event not in state["events"]:
                        state["events"][event] = [asdict(p) for p in client.list_presentations(event)]
                        if persist:
                            _save(state_path, state)
                    presentations = state["events"][event]
                except UsenixError as error:
                    summary["failed"] += 1
                    event_errors.append({"event": event, "error": str(error)})
                    state.setdefault("event_errors", {})[event] = str(error)
                    if persist:
                        _save(state_path, state)
                    print(f"[usenix] {event}: {error}", file=sys.stderr)
                    continue
                state.setdefault("event_errors", {}).pop(event, None)
                planned.extend((event, presentation) for presentation in presentations)
            if persist and not args.refresh:
                # Give all requested conferences a first pass before retrying failures.
                planned.sort(key=lambda item: state["papers"].get(item[1]["url"], {}).get("status") in {"failed", "unavailable"})
            for event, presentation in planned:
                if args.limit is not None and processed >= args.limit:
                    break
                url = presentation["url"]
                entry = state["papers"].get(url, {})
                native_record = entry.get("metadata_engine") == METADATA_ENGINE
                if persist and not args.refresh and native_record and entry.get("status") == "skipped_talk":
                    summary["skipped_talk"] += 1
                    continue
                prior_download = entry.get("download", {})
                if args.command == "download" and prior_download and not args.refresh and native_record:
                    if _verified_download(prior_download, args.output, event):
                        summary["verified"] += 1
                        continue
                processed += 1
                if args.command == "list":
                    listed.append({"conference": event, **presentation})
                    continue
                if args.command == "ingest" and not args.run:
                    listed.append({"conference": event, **presentation})
                    continue
                entry = state["papers"].setdefault(url, {})
                entry["attempts"] = entry.get("attempts", 0) + 1
                try:
                    cached = entry.get("paper")
                    if (cached and not args.refresh and cached.get("pdf_url")
                            and cached.get("translator_id") == USENIX_TRANSLATOR_ID and cached.get("zotero_item")):
                        paper = UsenixPaper(**{**cached, "authors": tuple(cached["authors"])})
                    else:
                        paper = client.get_paper(url)
                        entry["paper"] = paper.to_dict()
                        entry["metadata_engine"] = METADATA_ENGINE
                        if persist:
                            _save(state_path, state)  # Keep resolved metadata even if interrupted during PDF I/O.
                    if args.command == "download":
                        previous = entry.get("download", {})
                        changed = previous.get("url") is not None and previous["url"] != paper.pdf_url
                        # Native title cleanup may change a generated filename.
                        # Keep a verified older file only when its URL matches
                        # the PDF chosen by the current official translator.
                        download = (_verified_download(previous, args.output, event)
                                    if paper.pdf_url and previous.get("url") == paper.pdf_url else None)
                        if download is None:
                            download = client.download_pdf(paper, args.output,
                                                           expected_sha256=previous.get("sha256"), force=changed)
                        entry["download"] = download
                        status = download["status"]
                    else:
                        collection = bridge.get_or_create_collection(f"{event[:-2]}{paper.year}")
                        cids = [collection["id"]]
                        if args.project:
                            cids.append(bridge.get_or_create_collection(args.project)["id"])
                        imported = client.ingest_paper(bridge, paper, collection_ids=cids)
                        entry["zotero"] = imported
                        status = imported.get("pdf_status", "failed") if imported.get("status") == "success" else "failed"
                        if status == "existing":
                            processed -= 1
                    entry["status"] = status
                    entry.pop("error", None)
                except NotUsenixPaper as error:
                    processed -= 1
                    entry.update({"status": "skipped_talk", "error": str(error), "metadata_engine": METADATA_ENGINE})
                except (UsenixError, ZoteroBridgeError, OSError) as error:
                    entry.update({"status": "failed", "error": str(error)})
                summary[entry["status"]] += 1
                print(f"[usenix] {entry['status']}: {presentation['title']}", file=sys.stderr, flush=True)
                if persist:
                    _save(state_path, state)
    except KeyboardInterrupt:
        if persist:
            _save(state_path, state)
        print("[usenix] Interrupted; saved progress. Repeat the command to continue.", file=sys.stderr)
        return 130
    except (OSError, ZoteroBridgeError) as error:
        print(f"[usenix] {error}", file=sys.stderr)
        return 1
    if args.command == "list" or (args.command == "ingest" and not args.run):
        print(json.dumps({"presentations": listed, "event_errors": event_errors,
                          "preview": args.command == "ingest"}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"processed": processed, "summary": dict(summary), "state": str(state_path)}, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] or summary["unavailable"] else 0


if __name__ == "__main__":
    sys.exit(main())
