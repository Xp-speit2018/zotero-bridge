import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from zotero_bridge.export import Exporter, _inject_rdf_attachment_paths
from zotero_bridge.export_cli import _resolve_collection_id, main as export_main


class ExportPackageTests(unittest.TestCase):
    def test_inject_rdf_attachment_paths(self):
        rdf = '<z:Attachment rdf:about="#item_10">\n</z:Attachment>'

        patched = _inject_rdf_attachment_paths(rdf, [{"attachment_id": 10, "path": "attachments/paper.pdf"}])

        self.assertIn('<z:path rdf:resource="attachments/paper.pdf"/>', patched)

    def test_collection_package_writes_exports_manifest_and_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.pdf"
            source.write_bytes(b"%PDF test")

            exporter = Exporter(bridge=object())
            exporter.collection = lambda collection_id, format="zotero-rdf", options=None: (
                '<rdf:RDF><z:Attachment rdf:about="#item_20">\n</z:Attachment></rdf:RDF>'
                if format == "zotero-rdf"
                else f"{format} export"
            )
            exporter._collection_item_records = lambda collection_id: [{
                "id": 1,
                "itemID": 1,
                "key": "ABC",
                "title": "Example",
                "attachments": [{
                    "id": 20,
                    "itemID": 20,
                    "title": "PDF",
                    "filename": "source.pdf",
                    "contentType": "application/pdf",
                    "path": str(source),
                }],
            }]

            manifest = exporter.collection_package(123, tmp_path / "package")

            package = Path(manifest["output_dir"])
            self.assertEqual(manifest["item_count"], 1)
            self.assertEqual(manifest["copied_attachment_count"], 1)
            self.assertTrue((package / "collection.rdf").exists())
            self.assertTrue((package / "collection.bib").exists())
            self.assertTrue((package / "collection.ris").exists())
            self.assertEqual((package / "attachments" / "source.pdf").read_bytes(), b"%PDF test")

            rdf = (package / "collection.rdf").read_text(encoding="utf-8")
            self.assertIn('<z:path rdf:resource="attachments/source.pdf"/>', rdf)

            manifest_file = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_file["collection_id"], 123)

    def test_collection_package_can_zip_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            exporter = Exporter(bridge=object())
            exporter.collection = lambda collection_id, format="zotero-rdf", options=None: "export"
            exporter._collection_item_records = lambda collection_id: []

            manifest = exporter.collection_package(123, tmp_path / "package.zip", zip_output=True)

            self.assertTrue(Path(manifest["zip_path"]).exists())


class ExportCliTests(unittest.TestCase):
    def test_resolve_collection_id_by_name(self):
        class FakeBridge:
            def get_collections(self):
                return [{"id": 7, "name": "cxl-noob"}]

        self.assertEqual(_resolve_collection_id(FakeBridge(), "cxl-noob", None), 7)

    def test_cli_invokes_collection_package(self):
        class FakeExport:
            def __init__(self):
                self.calls = []

            def collection_package(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return {"ok": True}

        class FakeBridge:
            def __init__(self, *args, **kwargs):
                self.export = FakeExport()

            def get_collections(self):
                return [{"id": 7, "name": "cxl-noob"}]

        with patch("zotero_bridge.export_cli.ZoteroBridge", FakeBridge):
            with redirect_stdout(StringIO()):
                self.assertEqual(export_main(["--collection", "cxl-noob", "--output", "out", "--zip"]), 0)


if __name__ == "__main__":
    unittest.main()
