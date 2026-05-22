# zotero-bridge

[![PyPI](https://img.shields.io/pypi/v/zotero-bridge)](https://pypi.org/project/zotero-bridge/)
[![Python](https://img.shields.io/pypi/pyversions/zotero-bridge)](https://pypi.org/project/zotero-bridge/)
[![CI](https://github.com/Xp-speit2018/zotero-bridge/actions/workflows/publish.yml/badge.svg)](https://github.com/Xp-speit2018/zotero-bridge/actions/workflows/publish.yml)
[![License](https://img.shields.io/pypi/l/zotero-bridge)](LICENSE)

Python SDK for the [Zotero debug-bridge](https://github.com/retorquere/zotero-better-bibtex/tree/master/test/fixtures/debug-bridge) — programmatically manage your Zotero library via HTTP.

## Install

```bash
pip install zotero-bridge
```

Or from source:

```bash
git clone https://github.com/Xp-speit2018/zotero-bridge.git
cd zotero-bridge
pip install -e ".[dev]"
```

## Quick start

```python
from zotero_bridge import ZoteroBridge

bridge = ZoteroBridge()

# Lookup existing items
lookup = bridge.lookup("10.1109/DAC63849.2025.11132862", "DOI", include_attachments=True)
usenix = bridge.lookup("https://www.usenix.org/conference/osdi25/presentation/lou", "url")

# Backward-compatible duplicate check
dup = bridge.check_duplicate("10.1109/DAC63849.2025.11132862", "DOI")

# Add by identifier (magic wand)
item = bridge.add_by_identifier("10.1109/DAC63849.2025.11132862", "DOI")

# Auto-fetch PDF
bridge.find_fulltext(item["itemID"])

# Add note + tag
bridge.add_note(item["itemID"], "Key insight: ...")
bridge.add_tag(item["itemID"], "to-read")

# Download PDF bytes
pdf = bridge.get_pdf_bytes(item["itemID"])
```

## Configuration

Environment variables (optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_BRIDGE_URL` | `http://localhost:23120` | Debug-bridge proxy URL |
| `ZOTERO_BRIDGE_TOKEN` | `zotero-debug` | Bearer token |
| `ZOTERO_LIBRARY_ID` | *(empty)* | Library ID; empty = user library |

Or a `.env` file (requires `python-dotenv`):

```bash
ZOTERO_BRIDGE_URL=http://localhost:23120
ZOTERO_BRIDGE_TOKEN=zotero-debug
```

## CLI lookup

Look up existing Zotero items and print JSON:

```bash
zotero-lookup --doi "10.1109/DAC63849.2025.11132862" --attachments --notes
zotero-lookup --paper-url "https://www.usenix.org/conference/osdi25/presentation/lou" --attachments
zotero-lookup --title "Attention Is All You Need" --first
```

## CLI ingestion workflow

A ready-made pipeline that checks for duplicates, fetches metadata + PDF, creates DBLP-style venue collections, and aliases items into a project collection:

```bash
# Auto-derive venue from metadata
zotero-ingest --doi "10.1109/DAC63849.2025.11132862" --project "MyResearch"
zotero-ingest --paper-url "https://www.usenix.org/conference/osdi25/presentation/lou" --venue "OSDI 2025" --project "MyResearch"

# Or specify venue explicitly (still normalised to DBLP convention)
zotero-ingest --doi "10.1109/DAC63849.2025.11132862" --venue "ASPLOS" --project "MyResearch"
```

Note that metadata and pdf collection uses the built-in magic wand and `Find Full Text` functionality, which maybe paywalled or not depending on your network.

## CLI collection export

Export a collection into an importable directory or zip package:

```bash
zotero-export --collection "cxl-noob" --output cxl-noob-export --zip
zotero-export --collection-id 37 --output cxl-noob-export.zip --zip --overwrite
```

The package includes:

- `collection.rdf` with Zotero RDF metadata and child notes
- `collection.bib` and `collection.ris` fallback exports
- `attachments/` with copied attachment files when available
- `manifest.json` with item and attachment metadata

For Zotero RDF packages, copied attachment paths are added to the RDF so another
Zotero client can import `collection.rdf` together with the adjacent files.

## API overview

### Items

| Method | Description |
|--------|-------------|
| `lookup(identifier, id_type, include_notes=False, include_attachments=False, first_only=False)` | Look up Zotero items by DOI / ISBN / arXiv / URL / title |
| `check_duplicate(identifier, id_type)` | Backward-compatible first-match duplicate check |
| `add_by_identifier(identifier, id_type)` | Magic wand ingest |
| `find_fulltext(item_id)` | Auto-download PDF |
| `get_item(item_id)` | Retrieve metadata |
| `delete_item(item_id)` | Trash an item |
| `update_field(item_id, field, value)` | Update a single field |
| `add_tag(item_id, tag)` | Add a tag |
| `remove_tag(item_id, tag)` | Remove a tag |

### Notes

| Method | Description |
|--------|-------------|
| `add_note(item_id, note_text)` | Add a child note |
| `get_notes(item_id)` | List child notes |

### Attachments

| Method | Description |
|--------|-------------|
| `get_attachments(item_id)` | List all attachments with paths |
| `retrieve_pdf(item_id)` | Get PDF metadata |
| `get_pdf_bytes(item_id)` | Download raw PDF bytes |

### Collections

| Method | Description |
|--------|-------------|
| `create_collection(name, parent_id)` | Create a collection |
| `get_collections(parent_id)` | List collections |
| `get_or_create_collection(name, parent_id)` | Idempotent creation |
| `add_to_collection(item_id, collection_id)` | Alias / place item |
| `remove_from_collection(item_id, collection_id)` | Remove from collection |

### Export

| Method | Description |
|--------|-------------|
| `export.item(item_id, format, options)` | Export a single item |
| `export.items(item_ids, format, options)` | Export multiple items |
| `export.collection(collection_id, format, options)` | Export a whole collection |
| `export.collection_package(collection_id, output_path, ...)` | Export a collection as a directory/zip with notes, fallback exports, and copied attachments |
| `export.library(format, options)` | Export the entire library |
| `export.list_formats()` | List available export formats |

**Supported formats:** `better-bibtex`, `better-biblatex`, `bibtex`, `biblatex`, `ris`, `csl-json`, `csv`, `zotero-rdf`, `tei`, `cff`.

```python
# Better BibTeX with notes
bib = bridge.export.item(item_id, format="better-bibtex", options={"exportNotes": True})

# Full collection as RIS
ris = bridge.export.collection(collection_id, format="ris")

# Importable collection package with notes and attachments
manifest = bridge.export.collection_package(
    collection_id,
    "cxl-noob-export.zip",
    zip_output=True,
    overwrite=True,
)

# Entire library
bib = bridge.export.library(format="better-bibtex")
```

## DBLP venue naming

When the ingestion workflow auto-derives a venue name, it normalises to DBLP convention:

- `ISSTA 2023` → `issta2023`
- `ASPLOS 2025, Volume 1` → `asplos2025-1`
- `NeurIPS 2023, Volume 2` → `neurips2023-2`

A curated mapping of 50+ common venues + DBLP API fallback + local cache handles less common venues automatically.

## Requirements

- Python ≥ 3.10
- A running Zotero instance with the [debug-bridge extension](https://github.com/retorquere/zotero-better-bibtex/releases/tag/debug-bridge) installed

## Releases

| Version | Date | PyPI | Notes |
|---------|------|------|-------|
| 0.5.0 | 2026-05-22 | [zotero-bridge-0.5.0](https://pypi.org/project/zotero-bridge/0.5.0/) | Collection package export with notes, fallback formats, attachments, and `zotero-export` CLI |
| 0.4.0 | 2026-05-19 | [zotero-bridge-0.4.0](https://pypi.org/project/zotero-bridge/0.4.0/) | URL lookup/ingest and USENIX paper fallback |
| 0.3.0 | 2026-05-19 | [zotero-bridge-0.3.0](https://pypi.org/project/zotero-bridge/0.3.0/) | Public lookup API and `zotero-lookup` CLI |
| 0.2.1 | 2025-05-18 | [zotero-bridge-0.2.1](https://pypi.org/project/zotero-bridge/0.2.1/) | Fix PyPI project links |
| 0.2.0 | 2025-05-18 | [zotero-bridge-0.2.0](https://pypi.org/project/zotero-bridge/0.2.0/) | Export support (BibTeX, RIS, CSL JSON, etc.) |
| 0.1.0 | 2025-05-18 | [zotero-bridge-0.1.0](https://pypi.org/project/zotero-bridge/0.1.0/) | Initial release |


## Acknowledgements

This SDK is built on top of the **Zotero debug-bridge** extension by [Emile Sonneveld](https://github.com/retorquere) / [iris-advies.com](https://github.com/retorquere/zotero-better-bibtex/tree/master/test/fixtures/debug-bridge), originally distributed as part of the [zotero-better-bibtex](https://github.com/retorquere/zotero-better-bibtex) test fixtures. The debug-bridge enables arbitrary JavaScript execution inside a running Zotero instance via an authenticated HTTP endpoint, which is the foundation of everything this SDK does.

## License

MIT
