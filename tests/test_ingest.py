import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from zotero_bridge.ingest import ingest, ingest_webpage, main as ingest_main


class FakeBridge:
    def __init__(self, duplicate=None, existing_pdf=None):
        self.duplicate = duplicate or {"found": False}
        self.existing_pdf = existing_pdf
        self.calls = []

    def check_duplicate(self, identifier, id_type="DOI"):
        self.calls.append(("check_duplicate", identifier, id_type))
        return self.duplicate

    def add_by_identifier(self, *args, **kwargs):
        self.calls.append(("add_by_identifier", args, kwargs))
        raise AssertionError("identifier ingest should not be used")

    def get_or_create_collection(self, name):
        self.calls.append(("get_or_create_collection", name))
        return {"id": 7, "name": name}

    def add_to_collection(self, item_id, collection_id):
        self.calls.append(("add_to_collection", item_id, collection_id))
        return {"status": "success"}

    def create_item(self, **kwargs):
        self.calls.append(("create_item", kwargs))
        return {"status": "success", "itemID": 42, "key": "ABC"}

    def retrieve_pdf(self, item_id):
        self.calls.append(("retrieve_pdf", item_id))
        return self.existing_pdf

    def attach_file_from_url(self, item_id, url):
        self.calls.append(("attach_file_from_url", item_id, url))
        return {"status": "success", "attachmentID": 99}


class IngestWebpageTests(unittest.TestCase):
    def test_webpage_ingest_creates_webpage_without_identifier_lookup(self):
        bridge = FakeBridge()

        result = ingest_webpage(
            bridge,
            "https://doc.dpdk.org/guides/prog_guide/ring_lib.html",
            title="DPDK Ring Library",
            project="ringbuffer-history",
            author="DPDK Project",
            tags=["dpdk", "ring"],
        )

        self.assertEqual(result["action"], "created")
        call_names = [c[0] for c in bridge.calls]
        self.assertIn("create_item", call_names)
        self.assertNotIn("add_by_identifier", call_names)
        self.assertNotIn("attach_file_from_url", call_names)

    def test_webpage_ingest_attaches_direct_pdf_url(self):
        bridge = FakeBridge()

        result = ingest_webpage(
            bridge,
            "https://fast.dpdk.org/doc/pdf-guides/prog_guide-20.08.pdf",
            title="DPDK Programmer's Guide",
            project="ringbuffer-history",
        )

        self.assertEqual(result["attachment_result"]["status"], "success")
        self.assertIn(
            ("attach_file_from_url", 42, "https://fast.dpdk.org/doc/pdf-guides/prog_guide-20.08.pdf"),
            bridge.calls,
        )

    def test_title_only_ingest_does_not_attempt_unsupported_add(self):
        bridge = FakeBridge()
        err = io.StringIO()

        with redirect_stderr(err):
            result = ingest(bridge, "DPDK Ring Library", id_type="title", project="ringbuffer-history")

        self.assertEqual(result["action"], "failed")
        self.assertEqual(result["reason"], "title_lookup_only")
        self.assertIn("--webpage-url", err.getvalue())
        self.assertNotIn("add_by_identifier", [c[0] for c in bridge.calls])

    def test_cli_webpage_url_routes_to_webpage_ingest(self):
        fake = FakeBridge()

        with patch("zotero_bridge.ingest.ZoteroBridge", return_value=fake):
            with redirect_stdout(io.StringIO()):
                code = ingest_main([
                    "--webpage-url",
                    "https://doc.dpdk.org/guides/prog_guide/ring_lib.html",
                    "--title",
                    "DPDK Ring Library",
                    "--project",
                    "ringbuffer-history",
                    "--tag",
                    "dpdk",
                ])

        self.assertEqual(code, 0)
        self.assertIn("create_item", [c[0] for c in fake.calls])


if __name__ == "__main__":
    unittest.main()
