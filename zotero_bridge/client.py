"""Zotero debug-bridge client.

A thin Python wrapper around Zotero's HTTP debug-bridge endpoint.
Every public method maps to a small JavaScript payload that is executed
inside the running Zotero instance.

Configuration is read from environment variables when arguments are omitted::

    export ZOTERO_BRIDGE_URL="http://localhost:23120"
    export ZOTERO_BRIDGE_TOKEN="zotero-debug"
    export ZOTERO_LIBRARY_ID=""          # optional; empty = user library

Or load from a ``.env`` file if ``python-dotenv`` is installed.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

# Optional .env support
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


class ZoteroBridgeError(Exception):
    """Raised when the debug-bridge returns an error or the HTTP call fails."""

    def __init__(self, message: str, status_code: int | None = None, response_text: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class ZoteroBridge:
    """Client for Zotero's debug-bridge HTTP endpoint.

    Args:
        base_url: URL of the debug-bridge proxy. Defaults to ``ZOTERO_BRIDGE_URL`` env var,
            or ``http://localhost:23120``.
        token: Bearer token configured in Zotero's ``extensions.zotero.debug-bridge.token``.
            Defaults to ``ZOTERO_BRIDGE_TOKEN`` env var, or ``"zotero-debug"``.
        library_id: Zotero library ID to operate on. Defaults to ``ZOTERO_LIBRARY_ID`` env var,
            or ``None`` (user's personal library).
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        library_id: int | None = None,
    ):
        self.base_url = (base_url or os.getenv("ZOTERO_BRIDGE_URL", "http://localhost:23120")).rstrip("/")
        self.token = token or os.getenv("ZOTERO_BRIDGE_TOKEN", "zotero-debug")
        _lid = library_id if library_id is not None else os.getenv("ZOTERO_LIBRARY_ID", "")
        self.library_id = int(_lid) if str(_lid).strip() else None
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "text/plain",
        })

        # Lazy import to avoid circular dependency
        from .export import Exporter
        self.export = Exporter(self)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _exec(self, js_code: str) -> Any:
        """POST a JS snippet to ``/debug-bridge/execute`` and return the JSON result."""
        url = f"{self.base_url}/debug-bridge/execute"
        resp = self._session.post(url, data=js_code)

        if not resp.ok:
            raise ZoteroBridgeError(
                f"Debug-bridge returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                response_text=resp.text,
            )

        # The endpoint returns raw JSON text.
        text = resp.text
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _library_js(self) -> str:
        """Return JS that evaluates to the target library ID."""
        if self.library_id is not None:
            return str(self.library_id)
        return "Zotero.Libraries.userLibraryID"

    # ------------------------------------------------------------------ #
    # Items – lookup, ingest & duplicate check
    # ------------------------------------------------------------------ #

    def lookup(
        self,
        identifier: str,
        id_type: str = "DOI",
        *,
        include_notes: bool = False,
        include_attachments: bool = False,
        first_only: bool = False,
    ) -> dict[str, Any]:
        """Look up existing Zotero items by DOI, ISBN, arXiv ID, or title.

        Supported ``id_type`` values: ``"DOI"``, ``"ISBN"``, ``"arXiv"``, ``"title"``.

        Returns a dict with ``found``, ``count``, and ``matches``. Each match contains
        item metadata and can optionally include child notes and attachment metadata.
        """
        id_type = id_type.lower()
        if id_type == "doi":
            condition_js = f's.addCondition("DOI", "is", {json.dumps(identifier)});'
        elif id_type == "isbn":
            condition_js = f's.addCondition("ISBN", "is", {json.dumps(identifier)});'
        elif id_type == "arxiv":
            # arXiv IDs may live in the URL or Extra field.
            condition_js = (
                f's.addCondition("url", "contains", {json.dumps(identifier)});\n'
                f's.addCondition("extra", "contains", {json.dumps(identifier)});\n'
                's.addCondition("joinMode", "any");'
            )
        elif id_type == "title":
            condition_js = f's.addCondition("title", "contains", {json.dumps(identifier)});'
        else:
            raise ValueError(f"Unsupported id_type: {id_type}")

        first_only_js = "true" if first_only else "false"
        include_notes_js = "true" if include_notes else "false"
        include_attachments_js = "true" if include_attachments else "false"

        js = f"""
return (async () => {{
    var s = new Zotero.Search();
    s.libraryID = {self._library_js()};
    {condition_js}
    var ids = await s.search();
    if ({first_only_js}) ids = ids.slice(0, 1);

    async function serializeItem(item) {{
        var creators = [];
        for (var i = 0; i < item.numCreators(); i++) {{
            creators.push(item.getCreatorJSON(i));
        }}

        var notes = [];
        if ({include_notes_js}) {{
            var noteIDs = await item.getNotes();
            for (var nid of noteIDs) {{
                var n = await Zotero.Items.getAsync(nid);
                if (!n) continue;
                notes.push({{
                    id: n.id,
                    itemID: n.id,
                    key: n.key,
                    note: n.getNote(),
                    dateAdded: n.dateAdded,
                    dateModified: n.dateModified
                }});
            }}
        }}

        var attachments = [];
        if ({include_attachments_js}) {{
            var attIDs = await item.getAttachments();
            for (var aid of attIDs) {{
                var a = await Zotero.Items.getAsync(aid);
                if (!a) continue;
                var path = null;
                try {{ path = a.getFilePath(); }} catch (e) {{ path = null; }}
                attachments.push({{
                    id: a.id,
                    itemID: a.id,
                    key: a.key,
                    title: a.getField("title"),
                    contentType: a.attachmentContentType,
                    filename: a.attachmentFilename,
                    path: path,
                    isPDF: a.attachmentContentType === "application/pdf",
                    dateAdded: a.dateAdded,
                    dateModified: a.dateModified
                }});
            }}
        }}

        var result = {{
            id: item.id,
            itemID: item.id,
            key: item.key,
            libraryID: item.libraryID,
            itemType: Zotero.ItemTypes.getName(item.itemTypeID),
            title: item.getField("title"),
            date: item.getField("date"),
            DOI: item.getField("DOI"),
            ISBN: item.getField("ISBN"),
            url: item.getField("url"),
            publicationTitle: item.getField("publicationTitle"),
            conferenceName: item.getField("conferenceName"),
            proceedingsTitle: item.getField("proceedingsTitle"),
            volume: item.getField("volume"),
            issue: item.getField("issue"),
            publisher: item.getField("publisher"),
            place: item.getField("place"),
            abstractNote: item.getField("abstractNote"),
            extra: item.getField("extra"),
            creators: creators,
            tags: item.getTags().map(t => t.tag),
            collections: item.getCollections(),
            dateAdded: item.dateAdded,
            dateModified: item.dateModified
        }};
        if ({include_notes_js}) result.notes = notes;
        if ({include_attachments_js}) result.attachments = attachments;
        return result;
    }}

    var matches = [];
    for (var id of ids) {{
        var item = await Zotero.Items.getAsync(id);
        if (!item || !item.isRegularItem()) continue;
        matches.push(await serializeItem(item));
    }}
    return {{
        found: matches.length > 0,
        count: matches.length,
        query: {{ identifier: {json.dumps(identifier)}, id_type: {json.dumps(id_type)} }},
        matches: matches
    }};
}})();
"""
        return self._exec(js)

    def check_duplicate(
        self,
        identifier: str,
        id_type: str = "DOI",
    ) -> dict[str, Any]:
        """Check whether an item with the given identifier already exists.

        Supported ``id_type`` values: ``"DOI"``, ``"ISBN"``, ``"arXiv"``, ``"title"``.

        Returns a dict such as ``{"found": True, "itemID": 12345}`` or
        ``{"found": False}``. This compatibility wrapper delegates to ``lookup()``.
        """
        result = self.lookup(identifier, id_type, first_only=True)
        matches = result.get("matches") or []
        if not matches:
            return {"found": False}
        item = matches[0]
        return {
            "found": True,
            "itemID": item.get("itemID", item.get("id")),
            "key": item.get("key"),
            "title": item.get("title"),
            "DOI": item.get("DOI"),
        }

    def add_by_identifier(
        self,
        identifier: str,
        id_type: str = "DOI",
        collection_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Resolve an identifier into a full Zotero item ("magic wand" workflow).

        Uses ``Zotero.Translate.Search`` to fetch metadata, creates the item,
        optionally adds it to collections, and attempts to retrieve the PDF.

        Supported ``id_type`` values: ``"DOI"``, ``"ISBN"``, ``"arXiv"``.

        Returns a dict with ``status``, ``itemID``, ``key``, ``title``, etc.
        """
        id_type = id_type.lower()
        if id_type == "doi":
            search_obj = f'{{ DOI: {json.dumps(identifier)} }}'
        elif id_type == "isbn":
            search_obj = f'{{ ISBN: {json.dumps(identifier)} }}'
        elif id_type == "arxiv":
            search_obj = f'{{ arXiv: {json.dumps(identifier)} }}'
        else:
            raise ValueError(f"Unsupported id_type for add: {id_type}")

        collection_js = ""
        if collection_ids:
            cids = ",".join(str(cid) for cid in collection_ids)
            collection_js = f"""
    for (var cid of [{cids}]) {{
        zItem.addToCollection(cid);
    }}
"""

        js = f"""
return (async () => {{
    var translate = new Zotero.Translate.Search();
    translate.setSearch({search_obj});
    var tItems = [];
    translate.setHandler("itemDone", function(obj, item) {{ tItems.push(item); }});
    await translate.translate({{ libraryID: {self._library_js()} }});

    if (tItems.length === 0) return {{ status: "not_found" }};

    var t = tItems[0];
    var zItem = new Zotero.Item(t.itemType || "journalArticle");
    var fields = [
        "title","date","pages","DOI","url","publicationTitle",
        "conferenceName","volume","issue","publisher","language",
        "ISBN","place","libraryCatalog","accessDate","shortTitle",
        "abstractNote","edition","series","seriesNumber",
        "proceedingsTitle","journalAbbreviation","ISSN"
    ];
    for (var fn of fields) {{
        if (t[fn]) zItem.setField(fn, t[fn]);
    }}
    if (t.creators && t.creators.length) {{
        for (var i = 0; i < t.creators.length; i++) zItem.setCreator(i, t.creators[i]);
    }}
    {collection_js}
    await zItem.saveTx();

    return {{
        status: "success",
        itemID: zItem.id,
        key: zItem.key,
        title: zItem.getField("title"),
        DOI: zItem.getField("DOI"),
    }};
}})();
"""
        return self._exec(js)

    def find_fulltext(self, item_id: int) -> dict[str, Any]:
        """Attempt to download the PDF for an existing item.

        Returns a dict such as ``{"status": "success", "attachmentID": 123}``
        or ``{"status": "failed"}``.
        """
        js = f"""
return (async () => {{
    let item = await Zotero.Items.getAsync({item_id});
    let attachment = await Zotero.Attachments.addAvailableFile(item, {{
        methods: ["doi", "url", "oa", "custom"]
    }});
    return attachment
        ? {{ status: "success", attachmentID: attachment.id, title: attachment.getField("title") }}
        : {{ status: "failed" }};
}})();
"""
        return self._exec(js)

    # ------------------------------------------------------------------ #
    # Items – read / update / delete
    # ------------------------------------------------------------------ #

    def get_item(self, item_id: int) -> dict[str, Any]:
        """Retrieve metadata for a single item."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return null;
    var creators = [];
    for (var i = 0; i < item.numCreators(); i++) {{
        creators.push(item.getCreatorJSON(i));
    }}
    return {{
        id: item.id,
        key: item.key,
        itemType: Zotero.ItemTypes.getName(item.itemTypeID),
        title: item.getField("title"),
        date: item.getField("date"),
        DOI: item.getField("DOI"),
        url: item.getField("url"),
        publicationTitle: item.getField("publicationTitle"),
        conferenceName: item.getField("conferenceName"),
        proceedingsTitle: item.getField("proceedingsTitle"),
        volume: item.getField("volume"),
        issue: item.getField("issue"),
        publisher: item.getField("publisher"),
        place: item.getField("place"),
        abstractNote: item.getField("abstractNote"),
        creators: creators,
        tags: item.getTags().map(t => t.tag),
        collections: item.getCollections(),
    }};
}})();
"""
        return self._exec(js)

    def delete_item(self, item_id: int) -> dict[str, Any]:
        """Move an item to the trash."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return {{ deleted: false, reason: "not_found" }};
    item.deleted = true;
    await item.saveTx();
    return {{ deleted: true, itemID: {item_id} }};
}})();
"""
        return self._exec(js)

    def add_tag(self, item_id: int, tag: str) -> dict[str, Any]:
        """Add a tag to an existing item."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return {{ error: "item_not_found" }};
    item.addTag({json.dumps(tag)});
    await item.saveTx();
    return {{ status: "success", itemID: item.id, tags: item.getTags().map(t => t.tag) }};
}})();
"""
        return self._exec(js)

    def remove_tag(self, item_id: int, tag: str) -> dict[str, Any]:
        """Remove a tag from an item."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return {{ error: "item_not_found" }};
    item.removeTag({json.dumps(tag)});
    await item.saveTx();
    return {{ status: "success", itemID: item.id, tags: item.getTags().map(t => t.tag) }};
}})();
"""
        return self._exec(js)

    def update_field(self, item_id: int, field: str, value: str) -> dict[str, Any]:
        """Update a single field on an item (e.g. ``title``, ``abstractNote``)."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return {{ error: "item_not_found" }};
    item.setField({json.dumps(field)}, {json.dumps(value)});
    await item.saveTx();
    return {{ status: "success", itemID: item.id, field: {json.dumps(field)}, value: item.getField({json.dumps(field)}) }};
}})();
"""
        return self._exec(js)

    # ------------------------------------------------------------------ #
    # Notes
    # ------------------------------------------------------------------ #

    def add_note(self, item_id: int, note_text: str) -> dict[str, Any]:
        """Add a child note to an item."""
        js = f"""
return (async () => {{
    var note = new Zotero.Item('note');
    note.setNote({json.dumps(note_text)});
    note.parentID = {item_id};
    await note.saveTx();
    return {{ status: "success", noteID: note.id, parentID: {item_id} }};
}})();
"""
        return self._exec(js)

    def get_notes(self, item_id: int) -> list[dict[str, Any]]:
        """Return all child notes for an item."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return [];
    var noteIDs = await item.getNotes();
    var notes = [];
    for (var nid of noteIDs) {{
        var n = await Zotero.Items.getAsync(nid);
        notes.push({{ id: n.id, key: n.key, note: n.getNote() }});
    }}
    return notes;
}})();
"""
        return self._exec(js)

    # ------------------------------------------------------------------ #
    # Attachments (PDFs, etc.)
    # ------------------------------------------------------------------ #

    def get_attachments(self, item_id: int) -> list[dict[str, Any]]:
        """List all attachments for an item with file paths."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return [];
    var attIDs = await item.getAttachments();
    var atts = [];
    for (var aid of attIDs) {{
        var a = await Zotero.Items.getAsync(aid);
        atts.push({{
            id: a.id,
            key: a.key,
            title: a.getField("title"),
            contentType: a.attachmentContentType,
            path: a.getFilePath(),
            filename: a.attachmentFilename,
        }});
    }}
    return atts;
}})();
"""
        return self._exec(js)

    def retrieve_pdf(self, item_id: int) -> dict[str, Any] | None:
        """Return the first PDF attachment's metadata for an item, or ``None``."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return null;
    var attIDs = await item.getAttachments();
    for (var aid of attIDs) {{
        var a = await Zotero.Items.getAsync(aid);
        if (a.attachmentContentType === "application/pdf") {{
            return {{
                attachmentID: a.id,
                key: a.key,
                title: a.getField("title"),
                path: a.getFilePath(),
                filename: a.attachmentFilename,
            }};
        }}
    }}
    return null;
}})();
"""
        return self._exec(js)

    def get_pdf_bytes(self, item_id: int) -> bytes | None:
        """Return the raw PDF bytes for an item's first PDF attachment.

        The file is read inside Zotero and streamed back as base64.
        Suitable when the host does not have direct access to the storage volume.
        """
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return null;
    var attIDs = await item.getAttachments();
    for (var aid of attIDs) {{
        var a = await Zotero.Items.getAsync(aid);
        if (a.attachmentContentType === "application/pdf") {{
            var path = a.getFilePath();
            var bytes = await IOUtils.read(path);
            var chunkSize = 65536;
            var binary = "";
            for (var i = 0; i < bytes.length; i += chunkSize) {{
                var chunk = bytes.subarray(i, i + chunkSize);
                binary += String.fromCharCode.apply(null, chunk);
            }}
            return {{
                attachmentID: a.id,
                key: a.key,
                filename: a.attachmentFilename,
                base64: btoa(binary),
            }};
        }}
    }}
    return null;
}})();
"""
        import base64
        result = self._exec(js)
        if result is None:
            return None
        if isinstance(result, dict) and "base64" in result:
            return base64.b64decode(result["base64"])
        return None

    # ------------------------------------------------------------------ #
    # Collections
    # ------------------------------------------------------------------ #

    def create_collection(self, name: str, parent_id: int | None = None) -> dict[str, Any]:
        """Create a new collection. Returns ``{id, name, parentID}``."""
        parent_js = str(parent_id) if parent_id is not None else "false"
        js = f"""
return (async () => {{
    var collection = new Zotero.Collection();
    collection.libraryID = {self._library_js()};
    collection.name = {json.dumps(name)};
    collection.parentID = {parent_js};
    await collection.saveTx();
    return {{ id: collection.id, name: collection.name, parentID: collection.parentID }};
}})();
"""
        return self._exec(js)

    def get_collections(self, parent_id: int | None = None) -> list[dict[str, Any]]:
        """List collections. If ``parent_id`` is given, list only its children."""
        if parent_id is not None:
            js = f"""
return (async () => {{
    var cols = await Zotero.Collections.getByParent({parent_id});
    var result = [];
    for (var c of cols) {{
        result.push({{ id: c.id, name: c.name, parentID: c.parentID }});
    }}
    return result;
}})();
"""
        else:
            js = f"""
return (async () => {{
    var cols = await Zotero.Collections.getByLibrary({self._library_js()});
    var result = [];
    for (var c of cols) {{
        result.push({{ id: c.id, name: c.name, parentID: c.parentID }});
    }}
    return result;
}})();
"""
        return self._exec(js)

    def get_or_create_collection(self, name: str, parent_id: int | None = None) -> dict[str, Any]:
        """Return an existing collection by name, or create it."""
        existing = self.get_collections(parent_id=parent_id)
        for c in existing:
            if c["name"] == name:
                return c
        return self.create_collection(name, parent_id)

    def add_to_collection(self, item_id: int, collection_id: int) -> dict[str, Any]:
        """Add an item to a collection (alias / place)."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return {{ error: "item_not_found" }};
    item.addToCollection({collection_id});
    await item.saveTx();
    return {{ status: "success", itemID: {item_id}, collectionID: {collection_id} }};
}})();
"""
        return self._exec(js)

    def remove_from_collection(self, item_id: int, collection_id: int) -> dict[str, Any]:
        """Remove an item from a collection."""
        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) return {{ error: "item_not_found" }};
    item.removeFromCollection({collection_id});
    await item.saveTx();
    return {{ status: "success", itemID: {item_id}, collectionID: {collection_id} }};
}})();
"""
        return self._exec(js)
