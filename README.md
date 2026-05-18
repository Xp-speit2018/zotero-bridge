# zotero-bridge

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

# Duplicate check
dup = bridge.check_duplicate("10.1145/3597926.3598095", "DOI")

# Add by identifier (magic wand)
item = bridge.add_by_identifier("10.1145/3597926.3598095", "DOI")

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

## CLI ingestion workflow

A ready-made pipeline that checks for duplicates, fetches metadata + PDF, creates DBLP-style venue collections, and aliases items into a project collection:

```bash
# Auto-derive venue from metadata
zotero-ingest --doi "10.1145/3597926.3598095" --project "MyResearch"

# Or specify venue explicitly (still normalised to DBLP convention)
zotero-ingest --doi "10.1145/3597926.3598095" --venue "ASPLOS" --project "MyResearch"
```

## API overview

### Items

| Method | Description |
|--------|-------------|
| `check_duplicate(identifier, id_type)` | Query by DOI / ISBN / arXiv / title |
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

## DBLP venue naming

When the ingestion workflow auto-derives a venue name, it normalises to DBLP convention:

- `ISSTA 2023` → `issta2023`
- `ASPLOS 2025, Volume 1` → `asplos2025-1`
- `NeurIPS 2023, Volume 2` → `neurips2023-2`

A curated mapping of 50+ common venues + DBLP API fallback + local cache handles less common venues automatically.

## Requirements

- Python ≥ 3.10
- A running Zotero instance with the [debug-bridge extension](https://github.com/retorquere/zotero-better-bibtex/releases/tag/debug-bridge) installed

## License

MIT
