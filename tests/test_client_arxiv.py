import unittest

from zotero_bridge.client import (
    ZoteroBridge,
    _arxiv_pdf_url,
    _infer_arxiv_id_from_item,
    _normalize_arxiv_id,
)


class ArxivPdfTests(unittest.TestCase):
    def test_normalize_arxiv_id_from_common_forms(self):
        cases = {
            "2503.17864": "2503.17864",
            "arXiv:2503.17864v2": "2503.17864",
            "10.48550/arXiv.2503.17864": "2503.17864",
            "https://arxiv.org/abs/2503.17864": "2503.17864",
            "https://arxiv.org/pdf/2503.17864.pdf": "2503.17864",
            "hep-th/9901001v3": "hep-th/9901001",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(_normalize_arxiv_id(value), expected)

    def test_infer_arxiv_id_prefers_zotero_fields(self):
        item = {
            "DOI": "10.48550/arXiv.2503.17864",
            "url": "https://example.com/not-arxiv",
            "extra": "arXiv:1111.22222",
        }
        self.assertEqual(_infer_arxiv_id_from_item(item), "2503.17864")

    def test_arxiv_pdf_url(self):
        self.assertEqual(_arxiv_pdf_url("2503.17864"), "https://arxiv.org/pdf/2503.17864")

    def test_find_fulltext_falls_back_to_arxiv_pdf_url(self):
        class FakeBridge(ZoteroBridge):
            def __init__(self):
                self.calls = []

            def _exec(self, js_code):
                self.calls.append(js_code)
                if "addAvailableFile" in js_code:
                    return {"status": "failed"}
                if "importFromURL" in js_code:
                    return {
                        "status": "success",
                        "method": "arxiv-pdf-url",
                        "attachmentID": 42,
                        "pdfURL": "https://arxiv.org/pdf/2503.17864",
                    }
                raise AssertionError("unexpected JS")

            def get_item(self, item_id):
                return {
                    "id": item_id,
                    "DOI": "10.48550/arXiv.2503.17864",
                    "url": "https://arxiv.org/abs/2503.17864",
                    "extra": "arXiv:2503.17864",
                }

        bridge = FakeBridge()
        result = bridge.find_fulltext(123)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["method"], "arxiv-pdf-url")
        self.assertEqual(result["pdfURL"], "https://arxiv.org/pdf/2503.17864")
        self.assertEqual(len(bridge.calls), 2)
        self.assertIn("addAvailableFile", bridge.calls[0])
        self.assertIn("importFromURL", bridge.calls[1])


if __name__ == "__main__":
    unittest.main()
