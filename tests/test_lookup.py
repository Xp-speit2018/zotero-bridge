import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from zotero_bridge import ZoteroBridge
from zotero_bridge.lookup import main as lookup_main


class FakeBridge(ZoteroBridge):
    def __init__(self, result):
        self.result = result
        self.calls = []

    def lookup(self, identifier, id_type="DOI", **kwargs):
        self.calls.append((identifier, id_type, kwargs))
        return self.result


class LookupTests(unittest.TestCase):
    def test_check_duplicate_delegates_to_lookup_and_preserves_old_shape(self):
        bridge = FakeBridge({
            "found": True,
            "count": 2,
            "matches": [
                {"itemID": 123, "key": "ABC", "title": "Paper", "DOI": "10.1/example"},
                {"itemID": 456, "key": "DEF", "title": "Duplicate", "DOI": "10.1/example"},
            ],
        })

        result = bridge.check_duplicate("10.1/example", "DOI")

        self.assertEqual(result, {
            "found": True,
            "itemID": 123,
            "key": "ABC",
            "title": "Paper",
            "DOI": "10.1/example",
        })
        self.assertEqual(bridge.calls, [("10.1/example", "DOI", {"first_only": True})])

    def test_check_duplicate_returns_false_when_lookup_has_no_matches(self):
        bridge = FakeBridge({"found": False, "count": 0, "matches": []})
        self.assertEqual(bridge.check_duplicate("missing", "title"), {"found": False})

    def test_cli_maps_flags_to_lookup(self):
        fake = FakeBridge({"found": False, "count": 0, "matches": []})
        with patch("zotero_bridge.lookup.ZoteroBridge", return_value=fake):
            out = io.StringIO()
            with redirect_stdout(out):
                code = lookup_main(["--doi", "10.1/example", "--attachments", "--notes", "--first"])

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"found": False, "count": 0, "matches": []})
        self.assertEqual(fake.calls, [(
            "10.1/example",
            "DOI",
            {"include_notes": True, "include_attachments": True, "first_only": True},
        )])


if __name__ == "__main__":
    unittest.main()
