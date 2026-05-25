import unittest

from zotero_bridge import ZoteroBridge


class FakeBridge(ZoteroBridge):
    def __init__(self):
        self.js = None

    def _library_js(self):
        return "1"

    def _exec(self, js_code):
        self.js = js_code
        return {"status": "success", "itemID": 42, "collections": [7]}


class CreateItemTests(unittest.TestCase):
    def test_create_item_sets_fields_creators_tags_and_collections(self):
        bridge = FakeBridge()

        result = bridge.create_item(
            item_type="webpage",
            fields={"title": "DPDK Ring Library", "url": "https://example.com"},
            creators=[{"name": "DPDK Project", "creatorType": "author"}],
            tags=["ring-buffer"],
            collection_ids=[7],
        )

        self.assertEqual(result["status"], "success")
        self.assertIn('new Zotero.Item("webpage")', bridge.js)
        self.assertIn('"title": "DPDK Ring Library"', bridge.js)
        self.assertIn('"name": "DPDK Project"', bridge.js)
        self.assertIn('"ring-buffer"', bridge.js)
        self.assertIn("for (var cid of [7])", bridge.js)


if __name__ == "__main__":
    unittest.main()
