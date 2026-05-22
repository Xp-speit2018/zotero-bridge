"""Export utilities for Zotero items and collections.

Uses Zotero's built-in ``Zotero.Translate.Export`` engine with available
translators (BibTeX, RIS, CSL JSON, CSV, Better BibTeX, etc.).
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

# Common export translator IDs
_EXPORT_TRANSLATORS: dict[str, str] = {
    "bibtex": "9cb70025-a888-4a29-a210-93ec52da40d4",
    "biblatex": "b6e39b57-8942-4d11-8259-342c46ce395f",
    "better-bibtex": "f895aa0d-f28e-47fe-b247-2ea77c6ed583",
    "better-biblatex": "f895aa0d-f28e-47fe-b247-2ea77c6ed583",
    "ris": "32d59d2d-b65a-4da4-b0a3-bdd3cfb979e7",
    "csl-json": "bc03b4fe-436d-4a1f-ba59-de4d2d7a63f7",
    "csv": "25f4c5e2-d790-4daa-a667-797619c7e2f2",
    "zotero-rdf": "14763d24-8ba0-45df-8f52-b8d1108e7ac9",
    "tei": "032ae9b7-ab90-9205-a479-baf81f49184a",
    "citation-graph": "19afa3fd-1c7f-4eb8-a37e-8d07768493e8",
    "cff": "e782b521-99ed-47c7-b021-62351a0a4f91",
    "better-csl-json": "f4b52ab0-f878-4556-85a0-c7aeedd09dfc",
}


def _resolve_translator(format_name: str) -> str:
    """Map a friendly format name to a Zotero translator ID."""
    key = format_name.lower().strip()
    if key in _EXPORT_TRANSLATORS:
        return _EXPORT_TRANSLATORS[key]
    # Assume the user passed a raw translator ID
    return format_name


class Exporter:
    """Export client — wraps Zotero's translation engine.

    This class is instantiated automatically by ``ZoteroBridge``; you
    typically access it via ``bridge.export``.
    """

    def __init__(self, bridge: Any):
        self._bridge = bridge

    def item(
        self,
        item_id: int,
        format: str = "better-bibtex",
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export a single item and return the generated text.

        Args:
            item_id: Zotero item ID.
            format: Export format. Common values:
                ``bibtex``, ``biblatex``, ``better-bibtex``,
                ``better-biblatex``, ``ris``, ``csl-json``, ``csv``,
                ``zotero-rdf``, ``tei``, ``cff``.
            options: Optional translator display options, e.g.
                ``{"exportNotes": true, "exportFileData": false}``.

        Returns:
            The exported text (BibTeX, RIS, etc.).
        """
        translator_id = _resolve_translator(format)
        opts_js = json.dumps(options) if options else "null"

        js = f"""
return (async () => {{
    var item = await Zotero.Items.getAsync({item_id});
    if (!item) throw new Error("Item {item_id} not found");
    var translation = new Zotero.Translate.Export();
    translation.setItems([item]);
    translation.setTranslator({json.dumps(translator_id)});
    var opts = {opts_js};
    if (opts) translation.setDisplayOptions(opts);
    var output = "";
    translation.setHandler("done", function(obj, success) {{
        output = obj._io ? obj._io.string : "";
    }});
    await translation.translate({{ libraryID: item.libraryID }});
    return output;
}})();
"""
        return self._bridge._exec(js)

    def items(
        self,
        item_ids: list[int],
        format: str = "better-bibtex",
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export multiple items and return the generated text."""
        translator_id = _resolve_translator(format)
        opts_js = json.dumps(options) if options else "null"
        ids_js = ",".join(str(i) for i in item_ids)

        js = f"""
return (async () => {{
    var items = [];
    for (var id of [{ids_js}]) {{
        var item = await Zotero.Items.getAsync(id);
        if (item) items.push(item);
    }}
    if (items.length === 0) throw new Error("No items found");
    var translation = new Zotero.Translate.Export();
    translation.setItems(items);
    translation.setTranslator({json.dumps(translator_id)});
    var opts = {opts_js};
    if (opts) translation.setDisplayOptions(opts);
    var output = "";
    translation.setHandler("done", function(obj, success) {{
        output = obj._io ? obj._io.string : "";
    }});
    await translation.translate({{ libraryID: items[0].libraryID }});
    return output;
}})();
"""
        return self._bridge._exec(js)

    def collection(
        self,
        collection_id: int,
        format: str = "better-bibtex",
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export all items in a collection and return the generated text."""
        translator_id = _resolve_translator(format)
        opts_js = json.dumps(options) if options else "null"

        js = f"""
return (async () => {{
    var collection = await Zotero.Collections.getAsync({collection_id});
    if (!collection) throw new Error("Collection {collection_id} not found");
    var items = await collection.getChildItems();
    if (!items || items.length === 0) return "";
    var translation = new Zotero.Translate.Export();
    translation.setItems(items);
    translation.setTranslator({json.dumps(translator_id)});
    var opts = {opts_js};
    if (opts) translation.setDisplayOptions(opts);
    var output = "";
    translation.setHandler("done", function(obj, success) {{
        output = obj._io ? obj._io.string : "";
    }});
    await translation.translate({{ libraryID: collection.libraryID }});
    return output;
}})();
"""
        return self._bridge._exec(js)

    def collection_package(
        self,
        collection_id: int,
        output_path: str | Path,
        *,
        format: str = "zotero-rdf",
        include_notes: bool = True,
        include_files: bool = True,
        extra_formats: list[str] | None = None,
        zip_output: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Export a collection as an importable directory, optionally zipped.

        The package contains a primary metadata export, a ``manifest.json``
        summary, and, when requested, an ``attachments/`` directory populated
        with local attachment files. For Zotero RDF exports, copied attachment
        paths are added back into the RDF as ``z:path`` resources so another
        Zotero client can import the RDF together with the adjacent files.

        Args:
            collection_id: Zotero collection ID.
            output_path: Directory path, or zip path when ``zip_output=True``.
            format: Primary export format. Defaults to ``zotero-rdf`` because
                it preserves Zotero notes and collection metadata best.
            include_notes: Pass ``exportNotes`` to Zotero translators.
            include_files: Copy child attachment files into the package.
            extra_formats: Additional text exports to include. Defaults to
                ``["better-bibtex", "ris"]`` for a convenient fallback.
            zip_output: Create a zip archive after writing the directory.
            overwrite: Replace an existing output directory or zip file.

        Returns:
            A manifest dictionary describing the package contents.
        """
        extra_formats = ["better-bibtex", "ris"] if extra_formats is None else extra_formats
        output = Path(output_path)
        package_dir = output.with_suffix("") if zip_output and output.suffix == ".zip" else output

        if package_dir.exists():
            if not overwrite:
                raise FileExistsError(f"Output directory already exists: {package_dir}")
            if package_dir.is_dir():
                shutil.rmtree(package_dir)
            else:
                package_dir.unlink()
        package_dir.mkdir(parents=True, exist_ok=True)

        export_options = {"exportNotes": include_notes, "exportFileData": False}
        primary_text = self.collection(collection_id, format=format, options=export_options)

        records = self._collection_item_records(collection_id)
        copied = self._copy_collection_attachments(records, package_dir / "attachments") if include_files else []
        if format.lower().strip() == "zotero-rdf" and copied:
            primary_text = _inject_rdf_attachment_paths(primary_text, copied)

        primary_name = _format_filename(format)
        primary_path = package_dir / primary_name
        primary_path.write_text(primary_text, encoding="utf-8")

        extra_exports: list[dict[str, str]] = []
        for extra_format in extra_formats:
            if extra_format.lower().strip() == format.lower().strip():
                continue
            text = self.collection(collection_id, format=extra_format, options=export_options)
            path = package_dir / _format_filename(extra_format)
            path.write_text(text, encoding="utf-8")
            extra_exports.append({"format": extra_format, "path": str(path.relative_to(package_dir))})

        manifest = {
            "collection_id": collection_id,
            "format": format,
            "primary_export": str(primary_path.relative_to(package_dir)),
            "extra_exports": extra_exports,
            "include_notes": include_notes,
            "include_files": include_files,
            "item_count": len(records),
            "attachment_count": sum(len(r.get("attachments") or []) for r in records),
            "copied_attachment_count": len(copied),
            "items": records,
            "copied_attachments": copied,
        }
        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if zip_output:
            zip_path = output if output.suffix == ".zip" else output.with_suffix(".zip")
            if zip_path.exists():
                if not overwrite:
                    raise FileExistsError(f"Zip file already exists: {zip_path}")
                zip_path.unlink()
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in package_dir.rglob("*"):
                    if path.is_file():
                        zf.write(path, path.relative_to(package_dir))
            manifest["zip_path"] = str(zip_path)

        manifest["output_dir"] = str(package_dir)
        return manifest

    def library(
        self,
        format: str = "better-bibtex",
        options: dict[str, Any] | None = None,
    ) -> str:
        """Export the entire library and return the generated text."""
        translator_id = _resolve_translator(format)
        opts_js = json.dumps(options) if options else "null"
        library_js = (
            str(self._bridge.library_id)
            if self._bridge.library_id is not None
            else "Zotero.Libraries.userLibraryID"
        )

        js = f"""
return (async () => {{
    var libraryID = {library_js};
    var items = await Zotero.Items.getAll(libraryID, false, true, false);
    if (!items || items.length === 0) return "";
    var translation = new Zotero.Translate.Export();
    translation.setItems(items);
    translation.setTranslator({json.dumps(translator_id)});
    var opts = {opts_js};
    if (opts) translation.setDisplayOptions(opts);
    var output = "";
    translation.setHandler("done", function(obj, success) {{
        output = obj._io ? obj._io.string : "";
    }});
    await translation.translate({{ libraryID: libraryID }});
    return output;
}})();
"""
        return self._bridge._exec(js)

    def list_formats(self) -> list[dict[str, str]]:
        """Return all available export formats (translators) on this Zotero instance."""
        js = """
return (async () => {
    var translators = await Zotero.Translators.getAll();
    return translators
        .filter(t => t.translatorType === 2)
        .map(t => ({ label: t.label, translatorID: t.translatorID }));
})();
"""
        return self._bridge._exec(js)

    def _collection_item_records(self, collection_id: int) -> list[dict[str, Any]]:
        js = f"""
return (async () => {{
    var collection = await Zotero.Collections.getAsync({collection_id});
    if (!collection) throw new Error("Collection {collection_id} not found");
    var items = await collection.getChildItems();
    var result = [];
    for (var item of items) {{
        if (!item || !item.isRegularItem()) continue;
        var attachments = [];
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
                url: a.getField("url"),
            }});
        }}
        result.push({{
            id: item.id,
            itemID: item.id,
            key: item.key,
            itemType: Zotero.ItemTypes.getName(item.itemTypeID),
            title: item.getField("title"),
            DOI: item.getField("DOI"),
            url: item.getField("url"),
            tags: item.getTags().map(t => t.tag),
            attachments: attachments,
        }});
    }}
    return result;
}})();
"""
        return self._bridge._exec(js)

    def _attachment_bytes(self, attachment_id: int) -> bytes | None:
        js = f"""
return (async () => {{
    var a = await Zotero.Items.getAsync({attachment_id});
    if (!a || !a.isAttachment()) return null;
    var path = null;
    try {{ path = a.getFilePath(); }} catch (e) {{ path = null; }}
    if (!path) return null;
    var bytes = await IOUtils.read(path);
    var chunkSize = 65536;
    var binary = "";
    for (var i = 0; i < bytes.length; i += chunkSize) {{
        var chunk = bytes.subarray(i, i + chunkSize);
        binary += String.fromCharCode.apply(null, chunk);
    }}
    return {{
        attachmentID: a.id,
        filename: a.attachmentFilename,
        base64: btoa(binary),
    }};
}})();
"""
        result = self._bridge._exec(js)
        if not result:
            return None
        return base64.b64decode(result["base64"])

    def _copy_collection_attachments(
        self,
        records: list[dict[str, Any]],
        attachment_dir: Path,
    ) -> list[dict[str, Any]]:
        attachment_dir.mkdir(parents=True, exist_ok=True)
        copied: list[dict[str, Any]] = []
        used_names: set[str] = set()

        for record in records:
            for attachment in record.get("attachments") or []:
                att_id = attachment.get("id") or attachment.get("itemID")
                filename = attachment.get("filename") or attachment.get("title") or f"attachment-{att_id}"
                safe_name = _unique_filename(_safe_filename(filename), used_names)
                target = attachment_dir / safe_name

                source_path = attachment.get("path")
                copied_from = None
                source_is_file = False
                if source_path:
                    try:
                        source_is_file = Path(source_path).is_file()
                    except OSError:
                        source_is_file = False

                if source_is_file:
                    shutil.copy2(source_path, target)
                    copied_from = "path"
                else:
                    data = self._attachment_bytes(int(att_id)) if att_id else None
                    if data is None:
                        continue
                    target.write_bytes(data)
                    copied_from = "bridge"

                rel_path = target.relative_to(attachment_dir.parent).as_posix()
                copied.append({
                    "item_id": record.get("id") or record.get("itemID"),
                    "attachment_id": att_id,
                    "title": attachment.get("title"),
                    "content_type": attachment.get("contentType"),
                    "filename": filename,
                    "path": rel_path,
                    "copied_from": copied_from,
                })
        return copied


def _format_filename(format_name: str) -> str:
    key = format_name.lower().strip()
    extensions = {
        "better-bibtex": "bib",
        "bibtex": "bib",
        "better-biblatex": "bib",
        "biblatex": "bib",
        "ris": "ris",
        "csl-json": "json",
        "better-csl-json": "json",
        "zotero-rdf": "rdf",
        "csv": "csv",
        "tei": "tei.xml",
        "cff": "cff",
    }
    ext = extensions.get(key, "txt")
    return f"collection.{ext}"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ()]+", "_", name, flags=re.ASCII).strip(" .")
    return cleaned or "attachment"


def _unique_filename(name: str, used_names: set[str]) -> str:
    path = Path(name)
    stem = path.stem or "attachment"
    suffix = path.suffix
    candidate = f"{stem}{suffix}"
    counter = 2
    while candidate in used_names:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used_names.add(candidate)
    return candidate


def _inject_rdf_attachment_paths(rdf_text: str, attachments: list[dict[str, Any]]) -> str:
    patched = rdf_text
    for attachment in attachments:
        att_id = attachment.get("attachment_id")
        rel_path = attachment.get("path")
        if not att_id or not rel_path:
            continue
        marker = f'<z:Attachment rdf:about="#item_{att_id}">'
        start = patched.find(marker)
        if start == -1:
            continue
        end = patched.find("</z:Attachment>", start)
        if end == -1:
            continue
        block = patched[start:end]
        if "<z:path" in block:
            continue
        path_line = f"        <z:path rdf:resource={json.dumps(rel_path)}/>\n"
        patched = patched[:end] + path_line + patched[end:]
    return patched
