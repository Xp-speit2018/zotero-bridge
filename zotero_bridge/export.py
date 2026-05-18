"""Export utilities for Zotero items and collections.

Uses Zotero's built-in ``Zotero.Translate.Export`` engine with available
translators (BibTeX, RIS, CSL JSON, CSV, Better BibTeX, etc.).
"""

from __future__ import annotations

import json
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
