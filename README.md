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

For the USENIX conference CLI and native-translator APIs in this checkout,
install from source:

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

# Documentation/webpage items bypass magic-wand identifier lookup
zotero-ingest \
  --webpage-url "https://doc.dpdk.org/guides/prog_guide/ring_lib.html" \
  --title "DPDK Programmer's Guide: Ring Library" \
  --author "DPDK Project" \
  --project "MyResearch" \
  --tag dpdk --tag ring-buffer

# Direct PDF documentation can be attached explicitly
zotero-ingest \
  --webpage-url "https://doc.dpdk.org/guides/prog_guide/ring_lib.html" \
  --title "DPDK Programmer's Guide: Ring Library" \
  --attach-url "https://fast.dpdk.org/doc/pdf-guides/prog_guide-20.08.pdf" \
  --project "MyResearch"
```

Most scholarly identifiers use the built-in magic wand and `Find Full Text`
functionality, which may depend on publisher access. USENIX paper URLs use the
installed USENIX web translator and native ItemSaver, matching the Chrome
Connector workflow. See [USENIX conference downloads](#usenix-conference-downloads)
for batch downloads and collection imports.
For documentation or project websites, use `--webpage-url` instead; it creates
an explicit Zotero `webpage` item and does not try identifier/magic-wand ingest.

## USENIX conference downloads

Automate OSDI, NSDI and other USENIX events while reusing Zotero's existing
translation and save infrastructure:

```text
technical sessions → presentation URL → USENIX / Embedded Metadata translator
                                   → native item JSON → local PDF download
                                                      → ItemSaver → collection + attachments
```

`UsenixClient` discovers presentation URLs from official technical sessions and
uses Zotero's installed **USENIX web translator**, the same translator used by
the Chrome Connector. That translator delegates to Zotero's Embedded Metadata
translator. The SDK keeps its complete item JSON, including creators, fields,
notes, tags and attachment URLs. It does not parse citation tags or BibTeX,
split author names, guess PDF filenames or rank page links itself.

`get_paper()` runs `Zotero.Translate.Web` with `libraryID: false`, which reads
metadata without saving items. New imports pass that JSON to Zotero's native
`Translate.ItemSaver`, with the requested collection IDs. Zotero saves metadata
and downloads attachments using its associated-file and snapshot preferences.
Exact URL/DOI matches are reused; missing PDFs on existing items are repaired
using the translator's PDF URL. A missing or failed translator is recorded as
an error rather than falling back to independently assembled metadata.

A running Zotero instance with debug-bridge and the USENIX translator is required
for metadata resolution, including local `download` runs. `list` and ingestion
preview only enumerate URLs and do not require Zotero. Translator updates come
from Zotero; the SDK does not ship a separate copy. The adapter supports modern
`/conference/<event>/presentation/...` and older
`/conference/<event>/technical-sessions/presentation/...` URLs. Native translation
has been checked against OSDI 2026, NSDI 2025 and OSDI 2012 samples. Pre-Drupal
`static.usenix.org/events/...` archives require a separate adapter.

```bash
# List presentation candidates as JSON (no PDF downloads or Zotero writes)
zotero-usenix list --event osdi25 --event nsdi25

# Download both conferences; progress and SHA-256 digests go into state.json
zotero-usenix download --conference OSDI --conference NSDI --year 2025 \
  --output usenix-papers

# Download ten pending papers; repeat the identical command to continue
zotero-usenix download --event osdi25 --event nsdi25 \
  --output usenix-papers --limit 10

# Multiple years can be requested with repeated --year flags
zotero-usenix download --conference OSDI --year 2024 --year 2025 \
  --output usenix-papers

# Preview Zotero ingestion, then explicitly run it
zotero-usenix ingest --event osdi25 --project Systems
zotero-usenix ingest --event osdi25 --event nsdi25 --project Systems --run --limit 10
```

Ingestion adds each item to its event collection (for example, `osdi2025` or
`nsdi2025`) and optionally to the `--project` collection. New items are saved
with those collection IDs; matching existing items are added to the collections
and their paper PDF is checked. Existing metadata is retained. The generic
`zotero-ingest --paper-url` command also uses this USENIX path.

The SDK checks the official `robots.txt` and waits at least ten seconds between
its USENIX operations (or a larger declared crawl delay). Zotero handles requests
made inside translators and ItemSaver. For SDK catalogue and local PDF requests,
socket timeouts and retries are bounded, and transient HTTP errors and 429
responses honor `Retry-After`. Each locally downloaded PDF must have a PDF header
and EOF marker, and match `Content-Length` when supplied without content encoding.
Only then is it atomically moved into place. The manifest records the full native
item, translator ID/update date, source URLs, download paths, SHA-256 digests and
errors. Repeated download runs verify completed files and repair missing/corrupt
files. Resumption is at paper granularity.

Run one serial worker per manifest. `--refresh` re-fetches catalogues and runs the
installed translator again, which is useful after a translator update or an
event releases PDFs or replaces preprints. Records produced by the earlier
custom metadata parser are automatically translated again before reuse.
Without `--refresh`, resolved records with a PDF link and conference catalogues
are cached. Records without a public PDF are resolved again on the next run.
Presentations the translator does not identify as conference papers are skipped
as talks. `list` reports presentation candidates; paper metadata is validated during download/ingest.
`download` and `ingest --run` save progress after each paper and continue past
individual failures. They return a nonzero exit status for failures/unavailable
PDFs, and 130 after Ctrl-C. Ingestion verifies the selected URL's attachment
exists in Zotero; a slides attachment does not satisfy paper PDF availability.
Metadata success is reported separately from `pdf_status`:

| `pdf_status` | Meaning |
|--------------|---------|
| `downloaded` | A paper PDF was saved in this import |
| `existing` | The matching paper PDF already exists |
| `unavailable` | The translator did not supply a public paper PDF |
| `failed` | Metadata may have been saved, but the PDF was not saved successfully |
| `not_requested` | `download_pdf=False` was used |

Use `--bridge-url` or `ZOTERO_BRIDGE_URL` to select the bridge endpoint, and
`--timeout` to adjust socket timeouts (default 60 seconds). On an interrupted
import, rerunning checks the library for an exact match before saving again.

Talks and verified completed files do not consume `--limit`. Known talks are
cached until `--refresh`. Across all requested events, pending papers are given
a first pass before failed/unavailable records are retried, so one inaccessible
paper cannot keep a small batch from advancing to another conference.

```python
from zotero_bridge import UsenixClient, ZoteroBridge

bridge = ZoteroBridge(request_timeout=60)
url = "https://www.usenix.org/conference/nsdi25/presentation/wang-zixuan"
collection = bridge.get_or_create_collection("nsdi2025")

# Read metadata once, then download locally and save into a collection.
with UsenixClient(bridge=bridge) as client:
    paper = client.get_paper(url)  # Does not save any library items
    downloaded = client.download_pdf(paper, "usenix-papers")
    result = client.ingest_paper(bridge, paper, collection_ids=[collection["id"]])
    print(paper.title, downloaded["path"], result["itemID"], result["pdf_status"])

# One-call collection import; repeated calls reuse exact URL/DOI matches.
result = bridge.add_usenix_paper(url, collection_ids=[collection["id"]])

# Low-level preview exposes the complete translator JSON.
translated = bridge.translate_usenix_paper(url)
if translated["status"] == "success":
    print(translated["translatorID"], translated["item"]["creators"])
# save_translated_item() saves directly; add_usenix_paper() also deduplicates.
```

Official sources: [OSDI 2025 technical sessions](https://www.usenix.org/conference/osdi25/technical-sessions),
[NSDI 2025 technical sessions](https://www.usenix.org/conference/nsdi25/technical-sessions),
[an NSDI paper with final/prepublication/slide media](https://www.usenix.org/conference/nsdi25/presentation/wang-zixuan),
[USENIX robots.txt](https://www.usenix.org/robots.txt),
[Zotero's USENIX translator](https://github.com/zotero/translators/blob/master/USENIX.js),
and [Zotero's native ItemSaver](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/translation/translate_item.js).
USENIX states that papers/proceedings become freely available when the event
begins. Attendee-only ZIP archives and unreleased PDFs are not prerequisites for
this workflow.

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
| `add_by_url(url, collection_ids=None)` | Paper-page ingest; USENIX URLs use the USENIX adapter |
| `translate_usenix_paper(url)` | Read metadata/attachment URLs with the installed official USENIX translator, without saving |
| `save_translated_item(item, collection_ids=None, save_attachments=True)` | Save complete translator JSON and attachments with native ItemSaver; no deduplication |
| `add_usenix_paper(url, collection_ids=None, download_pdf=True)` | Translate, reuse exact URL/DOI matches, or save via native ItemSaver into collections |
| `attach_usenix_pdf(item_id)` | Resolve the item's USENIX page and repair/reuse its paper PDF |
| `find_fulltext(item_id)` | USENIX paper resolution or native PDF lookup with arXiv fallback |
| `attach_arxiv_pdf(item_id, arxiv_id=None)` | Attach `https://arxiv.org/pdf/<id>` when an item has arXiv metadata |
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

## Development and validation

Run the offline test suite from a source checkout:

```bash
python -m unittest discover -s tests -v
```

Node.js enables the tests that execute generated bridge JavaScript; those tests
are skipped when Node is unavailable. The suite currently has 56 tests covering
native metadata preservation, ItemSaver inputs, duplicate reuse, attachment
repair, local PDF validation, rate limits, cache migration and batch resumption.

Live validation on 2026-09-30 used Zotero 9.0.6 and the installed USENIX translator
(updated 2025-07-29). OSDI 2026, NSDI 2025 and OSDI 2012 samples translated
successfully, and OSDI/NSDI 2026 keynotes without papers were skipped. An NSDI
ODRP paper was saved into a designated validation collection with all six authors
and its PDF; repeating the import reused the same item and attachment. Local CLI
resumption also upgraded a legacy metadata cache and retained its verified PDF.

## Releases

| Version | Date | PyPI | Notes |
|---------|------|------|-------|
| 0.5.1 | 2026-05-22 | [zotero-bridge-0.5.1](https://pypi.org/project/zotero-bridge/0.5.1/) | Add deterministic arXiv PDF attachment fallback for ingest/full-text lookup |
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
