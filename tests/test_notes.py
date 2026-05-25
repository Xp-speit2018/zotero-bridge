import unittest

from zotero_bridge import ZoteroBridge


class FakeBridge(ZoteroBridge):
    def __init__(self):
        self.js = None

    def _library_js(self):
        return "1"

    def _exec(self, js_code):
        self.js = js_code
        return {"status": "success", "noteID": 99, "collections": [7]}


class NoteTests(unittest.TestCase):
    def test_add_standalone_note_can_place_note_in_collections(self):
        bridge = FakeBridge()

        result = bridge.add_standalone_note("<p>Summary</p>", collection_ids=[7, 8])

        self.assertEqual(result["status"], "success")
        self.assertIn("new Zotero.Item('note')", bridge.js)
        self.assertIn("note.libraryID = 1", bridge.js)
        self.assertIn("note.setNote", bridge.js)
        self.assertIn("for (var cid of [7,8])", bridge.js)
        self.assertIn("note.addToCollection(cid)", bridge.js)


if __name__ == "__main__":
    unittest.main()
