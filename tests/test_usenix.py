import copy
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from zotero_bridge import UsenixClient, ZoteroBridge, ZoteroBridgeError
from zotero_bridge.ingest import ingest
from zotero_bridge.usenix import (
    NotUsenixPaper, UsenixError, UsenixPaper, UsenixPresentation,
    METADATA_ENGINE, USENIX_TRANSLATOR_ID, canonical_paper_url, conference_id, paper_from_translator, parse_sessions, pdf_integrity,
)
from zotero_bridge.usenix_cli import main


URL = "https://www.usenix.org/conference/nsdi25/presentation/wang-zixuan"
PDF_URL = "https://www.usenix.org/system/files/nsdi25-wang-zixuan.pdf"
PDF = b"%PDF-1.7\nsynthetic test file\n%%EOF\n"
TRANSLATED = json.loads((Path(__file__).parent / "fixtures" / "usenix-wang-translator.json").read_text())


def translated_paper():
    return paper_from_translator(copy.deepcopy(TRANSLATED), URL)


def response(content=b"", status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = content
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


def client_with_responses(*responses):
    session = Mock()
    session.get.side_effect = responses
    client = UsenixClient(session=session)
    client._pace = Mock()
    return client, session


class MetadataTests(unittest.TestCase):
    def test_conference_and_url_normalization(self):
        self.assertEqual(conference_id("OSDI", 2025), "osdi25")
        self.assertEqual(conference_id("USENIXSECURITY", 2024), "usenixsecurity24")
        self.assertEqual(canonical_paper_url(URL.replace("https://www.", "http://") + "/?x=1#media"), URL)
        self.assertIn("technical-sessions/presentation", canonical_paper_url(
            "https://www.usenix.org/conference/osdi12/technical-sessions/presentation/nightingale"))
        for value in [URL.replace("usenix.org", "usenix.org.evil.test"), "https://www.usenix.org/user/login", "https://user@www.usenix.org/conference/nsdi25/presentation/a"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_paper_url(value)

    def test_directory_deduplicates_and_excludes_joint_keynote(self):
        html = '''<article class="node-paper"><h2><a href="/conference/osdi25/presentation/a">Paper A</a></h2>
        <a href="/conference/osdi25/presentation/a"><img src="pdf.svg"></a></article>
        <article class="node-paper"><h2><a href="/conference/atc25/presentation/keynote">Joint keynote</a></h2></article>
        <article class="node-paper"><h2><a href="/conference/osdi25/presentation/b">Paper B</a></h2></article>'''
        papers = parse_sessions(html, "osdi25")
        self.assertEqual([p.title for p in papers], ["Paper A", "Paper B"])

    def test_older_directory_path(self):
        html = '<h2><a href="/conference/osdi12/technical-sessions/presentation/a">Older Paper</a></h2>'
        self.assertEqual(len(parse_sessions(html, "osdi12")), 1)

    def test_metadata_and_creators_are_kept_from_native_translator(self):
        result = copy.deepcopy(TRANSLATED)
        result["item"]["creators"].append({"name": "Research Team", "creatorType": "author", "fieldMode": 1})
        paper = paper_from_translator(result, URL)
        self.assertEqual(paper.pdf_url, PDF_URL)
        self.assertEqual(paper.pdf_version, "translator")
        self.assertEqual(paper.isbn, "9781939133465")
        self.assertEqual(paper.pages, "1101-1115")
        self.assertEqual(paper.zotero_item, result["item"])
        self.assertEqual(paper.zotero_item["language"], "en")
        self.assertEqual(paper.authors[-1], "Research Team")

    def test_only_translator_attachments_determine_the_pdf(self):
        result = copy.deepcopy(TRANSLATED)
        result["item"]["attachments"] = [{"url": PDF_URL.replace(".pdf", "_1.pdf"), "mimeType": "application/pdf"}]
        self.assertTrue(paper_from_translator(result, URL).pdf_url.endswith("_1.pdf"))
        result["item"]["attachments"] = [{"url": URL, "mimeType": "text/html"}]
        self.assertIsNone(paper_from_translator(result, URL).pdf_url)

    def test_translator_failure_does_not_fall_back_to_custom_metadata(self):
        for result in [{"status": "failed", "reason": "translator_missing"}, {**TRANSLATED, "translatorID": "other-translator"}]:
            with self.assertRaises(UsenixError):
                paper_from_translator(result, URL)

    def test_keynote_and_empty_program_are_reported(self):
        result = copy.deepcopy(TRANSLATED)
        result["item"]["itemType"] = "webpage"
        with self.assertRaises(NotUsenixPaper):
            paper_from_translator(result, URL)
        with self.assertRaises(UsenixError):
            parse_sessions("<h1>Program coming soon</h1>", "osdi25")

    def test_get_paper_uses_native_translator_instead_of_python_html_parser(self):
        client, session = client_with_responses()
        client._check_robots = Mock()
        client.bridge = Mock()
        client.bridge.translate_usenix_paper.return_value = copy.deepcopy(TRANSLATED)
        paper = client.get_paper(URL)
        client.bridge.translate_usenix_paper.assert_called_once_with(URL)
        session.get.assert_not_called()
        self.assertEqual(paper.translator_id, USENIX_TRANSLATOR_ID)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.paper = translated_paper()

    def test_atomic_pdf_download_verification_and_corruption_repair(self):
        client, session = client_with_responses(response(PDF), response(PDF))
        client._check_robots = Mock()
        with tempfile.TemporaryDirectory() as output:
            first = client.download_pdf(self.paper, output)
            second = client.download_pdf(self.paper, output, expected_sha256=first["sha256"])
            self.assertEqual(second["status"], "existing")
            self.assertEqual(session.get.call_count, 1)
            Path(first["path"]).write_bytes(b"<html>Not a PDF</html>")
            repaired = client.download_pdf(self.paper, output, expected_sha256=first["sha256"])
            self.assertEqual(repaired["status"], "downloaded")
            self.assertEqual(repaired["sha256"], hashlib.sha256(PDF).hexdigest())
            self.assertEqual(session.get.call_count, 2)
            self.assertEqual(list(Path(output).rglob("*.part")), [])

    def test_rejected_response_preserves_completed_file(self):
        client, _ = client_with_responses(response(PDF), response(b"<html>Login</html>"))
        client._check_robots = Mock()
        with tempfile.TemporaryDirectory() as output:
            downloaded = client.download_pdf(self.paper, output)
            with self.assertRaises(UsenixError):
                client.download_pdf(self.paper, output, force=True)
            self.assertEqual(Path(downloaded["path"]).read_bytes(), PDF)
            self.assertEqual(list(Path(output).rglob("*.part")), [])

    def test_truncated_pdf_and_wrong_length_are_rejected(self):
        client, _ = client_with_responses(response(PDF, headers={"Content-Length": "9000"}))
        client._check_robots = Mock()
        with tempfile.TemporaryDirectory() as output:
            with self.assertRaises(UsenixError):
                client.download_pdf(self.paper, output)
            path = Path(output) / "truncated.pdf"
            path.write_bytes(b"%PDF-1.7\ntruncated")
            with self.assertRaises(UsenixError):
                pdf_integrity(path)

    def test_rate_limit_retries_obey_retry_after_and_timeouts(self):
        client, session = client_with_responses(response(status=429, headers={"Retry-After": "30"}), response(PDF))
        with patch("zotero_bridge.usenix.time.sleep") as sleep:
            self.assertEqual(client._request(PDF_URL).content, PDF)
        sleep.assert_called_once_with(30.0)
        self.assertEqual(session.get.call_args.kwargs["timeout"], (10, 60))

    def test_access_denied_is_not_retried(self):
        client, session = client_with_responses(response(status=403))
        with self.assertRaisesRegex(UsenixError, "HTTP 403"):
            client._request(PDF_URL)
        self.assertEqual(session.get.call_count, 1)

    def test_timeout_retries_are_bounded(self):
        client, session = client_with_responses(requests.Timeout("stalled"), requests.Timeout("stalled"), requests.Timeout("stalled"))
        with self.assertRaises(UsenixError):
            client._request(PDF_URL)
        self.assertEqual(session.get.call_count, 3)

    def test_robots_rules_are_applied_before_target_request(self):
        client, session = client_with_responses(response(b"User-agent: *\nCrawl-delay: 15\nDisallow: /system/files/"))
        with self.assertRaisesRegex(UsenixError, "disallows"):
            client._check_robots(PDF_URL)
        self.assertEqual(client.interval, 15)
        self.assertEqual(session.get.call_count, 1)

    def test_pacing_never_requests_more_frequently_than_ten_seconds(self):
        client = UsenixClient()
        with patch("zotero_bridge.usenix.time.monotonic", side_effect=[100, 102, 110]), patch("zotero_bridge.usenix.time.sleep") as sleep:
            client._pace()
            client._pace()
        sleep.assert_called_once_with(8)
        with self.assertRaises(ValueError):
            UsenixClient(interval=1)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.paper = translated_paper()
        self.client = UsenixClient()
        self.client._pace = Mock()
        self.client._check_robots = Mock()
        self.bridge = Mock()
        self.bridge.lookup.return_value = {"matches": []}
        self.bridge.save_translated_item.return_value = {"status": "success", "itemID": 42, "key": "ABC", "attachments": [{"id": 99, "url": PDF_URL, "contentType": "application/pdf", "fileExists": True}]}
        self.bridge.get_attachments.return_value = []
        self.bridge.attach_file_from_url.return_value = {"status": "success", "attachmentID": 99}

    def test_new_item_uses_conference_metadata_and_selected_pdf(self):
        result = self.client.ingest_paper(self.bridge, self.paper, collection_ids=[7])
        self.assertEqual(result["pdf_status"], "downloaded")
        self.bridge.save_translated_item.assert_called_once_with(self.paper.zotero_item, collection_ids=[7], save_attachments=True)
        self.bridge.create_item.assert_not_called()
        self.bridge.attach_file_from_url.assert_not_called()

    def test_duplicate_url_and_existing_paper_pdf_are_reused(self):
        self.bridge.lookup.return_value = {"matches": [{"itemID": 42, "url": URL + "/?track=1"}]}
        self.bridge.get_attachments.return_value = [{"id": 99, "url": PDF_URL, "contentType": "application/pdf", "fileExists": True}]
        result = self.client.ingest_paper(self.bridge, self.paper, collection_ids=[7])
        self.assertEqual(result["action"], "existing")
        self.assertEqual(result["pdf_status"], "existing")
        self.bridge.create_item.assert_not_called()
        self.bridge.attach_file_from_url.assert_not_called()
        self.bridge.add_to_collection.assert_called_once_with(42, 7)

    def test_substring_url_match_does_not_reuse_wrong_item(self):
        short = replace(self.paper, url=URL.replace("wang-zixuan", "wang"))
        self.bridge.lookup.return_value = {"matches": [{"itemID": 88, "url": URL}]}
        self.assertEqual(self.client.ingest_paper(self.bridge, short)["action"], "created")

    def test_missing_attachment_is_repaired_and_slides_are_ignored(self):
        self.bridge.lookup.return_value = {"matches": [{"itemID": 42, "url": URL}]}
        self.bridge.get_attachments.return_value = [
            {"id": 98, "url": PDF_URL.replace(".pdf", "_slides.pdf"), "contentType": "application/pdf", "fileExists": True},
            {"id": 99, "url": PDF_URL, "contentType": "application/pdf", "fileExists": False}]
        result = self.client.ingest_paper(self.bridge, self.paper)
        self.assertEqual(result["pdf_status"], "downloaded")

    def test_pdf_failure_is_distinct_from_metadata_success(self):
        self.bridge.save_translated_item.return_value = {"status": "success", "itemID": 42, "attachments": [], "attachment_errors": [{"error": "HTTP 403"}]}
        result = self.client.ingest_paper(self.bridge, self.paper)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["pdf_status"], "failed")
        missing = self.client.ingest_paper(self.bridge, replace(self.paper, pdf_url=None))
        self.assertEqual(missing["pdf_status"], "unavailable")

    def test_metadata_only_import_disables_native_attachments(self):
        result = self.client.ingest_paper(self.bridge, self.paper, download_pdf=False)
        self.assertEqual(result["pdf_status"], "not_requested")
        self.bridge.save_translated_item.assert_called_once_with(
            self.paper.zotero_item, collection_ids=None, save_attachments=False)
        self.bridge.attach_file_from_url.assert_not_called()

    def test_native_save_failure_is_reported_without_custom_item_fallback(self):
        self.bridge.save_translated_item.return_value = {"status": "failed", "reason": "native_save_failed"}
        result = self.client.ingest_paper(self.bridge, self.paper)
        self.assertEqual(result["action"], "failed")
        self.bridge.create_item.assert_not_called()
        self.bridge.attach_file_from_url.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the generated bridge JavaScript")
    def test_native_saver_receives_complete_creators_notes_tags_and_collections(self):
        translated = copy.deepcopy(self.paper.zotero_item)
        translated["creators"].append({"name": "Research Group", "creatorType": "author"})
        translated["notes"] = [{"note": "<p>Translator note</p>"}]
        translated["tags"] = [{"tag": "RDMA"}]
        bridge = ZoteroBridge(library_id=8)
        for save_attachments in (True, False):
            generated = []
            with patch.object(bridge, "_exec", side_effect=lambda js: generated.append(js)):
                bridge.save_translated_item(translated, [7, 9], save_attachments=save_attachments)
            setup = '''
let received;
class ItemSaver {
  static ATTACHMENT_MODE_DOWNLOAD = 1;
  static ATTACHMENT_MODE_IGNORE = 0;
  constructor(options) { this.options = options; }
  async saveItems(items, callback) {
    received = {options: this.options, items};
    return [{ id: 42, key: "ABC", isRegularItem: () => true,
      getField: field => items[0][field], getAttachments: () => [],
      getCollections: () => this.options.collections }];
  }
}
const Zotero = { Translate: { ItemSaver } };
'''
            script = setup + "const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;\n" + \
                "new AsyncFunction('Zotero', " + json.dumps(generated[0]) + ")(Zotero).then(result => process.stdout.write(JSON.stringify({result, received}))).catch(e => { console.error(e); process.exit(1); });"
            executed = subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)
            saved = json.loads(executed.stdout)
            expected = copy.deepcopy(translated)
            if not save_attachments:
                expected["attachments"] = []
            self.assertEqual(saved["received"]["items"], [expected])
            self.assertEqual(saved["received"]["options"]["libraryID"], 8)
            self.assertEqual(saved["received"]["options"]["collections"], [7, 9])
            self.assertEqual(saved["received"]["options"]["attachmentMode"], 1 if save_attachments else 0)
            self.assertEqual(saved["result"]["itemID"], 42)
        self.assertEqual(translated["attachments"], self.paper.zotero_item["attachments"])

    def test_existing_add_by_url_routes_to_usenix_sdk(self):
        bridge = ZoteroBridge()
        with patch.object(bridge, "add_usenix_paper", return_value={"status": "success"}) as add:
            bridge.add_by_url(URL, collection_ids=[7])
        add.assert_called_once_with(URL, collection_ids=[7])

    def test_find_fulltext_for_usenix_resolves_paper_before_generic_pdf_search(self):
        bridge = ZoteroBridge()
        with patch.object(bridge, "get_item", return_value={"url": URL}), patch.object(bridge, "_exec") as execute, patch.object(bridge, "attach_usenix_pdf", return_value={"status": "success", "attachmentID": 99}) as attach:
            self.assertEqual(bridge.find_fulltext(42)["attachmentID"], 99)
        attach.assert_called_once_with(42)
        execute.assert_not_called()

    def test_existing_ingest_command_uses_exact_usenix_path(self):
        self.bridge.add_usenix_paper.return_value = {
            "status": "success", "action": "existing", "itemID": 42, "pdf_status": "downloaded"}
        self.bridge.get_item.return_value = {"conferenceName": "NSDI", "date": "2025"}
        self.bridge.get_or_create_collection.return_value = {"id": 7}
        with redirect_stdout(io.StringIO()):
            result = ingest(self.bridge, URL, "url", venue="NSDI 2025", project="Test")
        self.assertEqual(result["pdf_status"], "downloaded")
        self.bridge.check_duplicate.assert_not_called()
        self.bridge.find_fulltext.assert_not_called()

    def test_bridge_timeout_becomes_sdk_error(self):
        bridge = ZoteroBridge(request_timeout=12)
        bridge._session = Mock()
        bridge._session.post.side_effect = requests.Timeout("stalled")
        with self.assertRaises(ZoteroBridgeError):
            bridge._exec("return 1;")
        self.assertEqual(bridge._session.post.call_args.kwargs["timeout"], (10, 12))

    @unittest.skipUnless(shutil.which("node"), "Node is required to execute the generated bridge JavaScript")
    def test_native_attachment_query_handles_file_and_link_attachments(self):
        bridge = ZoteroBridge()
        generated = []
        with patch.object(bridge, "_exec", side_effect=lambda js: generated.append(js)):
            bridge.get_attachments(42)
        # Link attachments reject all file-only accessors, as Zotero does.
        setup = '''
const file = { id: 1, key: "PDF", attachmentContentType: "application/pdf",
  isFileAttachment: () => true, fileExists: async () => true,
  getFilePath: () => "/storage/paper.pdf", attachmentFilename: "paper.pdf",
  getField: field => field === "url" ? "https://www.usenix.org/system/files/paper.pdf" : "Paper" };
const link = { id: 2, key: "LINK", attachmentContentType: "",
  isFileAttachment: () => false,
  fileExists: () => { throw new Error("fileExists cannot be called on link attachments"); },
  getFilePath: () => { throw new Error("No path for link attachment"); },
  get attachmentFilename() { throw new Error("No filename for link attachment"); },
  getField: field => field === "url" ? "https://www.usenix.org/conference/nsdi25" : "Webpage" };
const Zotero = { Items: { getAsync: async id => id === 42 ? {getAttachments: async () => [1, 2]} : id === 1 ? file : link } };
'''
        script = setup + "const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;\n" + \
            "new AsyncFunction('Zotero', " + json.dumps(generated[0]) + ")(Zotero).then(r => process.stdout.write(JSON.stringify(r))).catch(e => { console.error(e); process.exit(1); });"
        result = subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)
        attachments = json.loads(result.stdout)
        self.assertTrue(attachments[0]["fileExists"])
        self.assertFalse(attachments[1]["fileExists"])
        self.assertIsNone(attachments[1]["path"])


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.paper = translated_paper()
        self.other = replace(self.paper, url=URL.replace("wang-zixuan", "du"), title="Second Paper",
                             pdf_url=PDF_URL.replace("wang-zixuan", "du"))
        self.client, _ = client_with_responses(response(PDF), response(PDF), response(PDF))
        self.client._check_robots = Mock()
        self.client.list_presentations = Mock(return_value=[UsenixPresentation(p.url, p.title) for p in [self.paper, self.other]])
        self.client.get_paper = Mock(side_effect=lambda url: self.paper if url == URL else self.other)

    def run_cli(self, argv):
        with patch("zotero_bridge.usenix_cli.UsenixClient", return_value=self.client), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(argv)

    def test_repeated_limited_batch_advances_then_repairs_deleted_pdf(self):
        with tempfile.TemporaryDirectory() as output:
            argv = ["download", "--event", "nsdi25", "--output", output, "--limit", "1"]
            self.assertEqual(self.run_cli(argv), 0)
            self.assertEqual(self.run_cli(argv), 0)
            state_path = Path(output) / "state.json"
            state = json.loads(state_path.read_text())
            self.assertEqual(len(state["papers"]), 2)
            self.assertEqual(self.client.get_paper.call_count, 2)
            Path(state["papers"][URL]["download"]["path"]).unlink()
            self.assertEqual(self.run_cli(argv), 0)
            self.assertTrue(Path(state["papers"][URL]["download"]["path"]).exists())

    def test_ingest_preview_writes_neither_library_nor_manifest(self):
        with tempfile.TemporaryDirectory() as output, patch("zotero_bridge.usenix_cli.ZoteroBridge") as bridge:
            self.assertEqual(self.run_cli(["ingest", "--event", "nsdi25", "--output", output]), 0)
            bridge.assert_not_called()
            self.client.get_paper.assert_not_called()
            self.assertEqual(list(Path(output).iterdir()), [])

    def test_legacy_completed_download_is_retranslated_before_reuse(self):
        with tempfile.TemporaryDirectory() as output:
            path = Path(output) / "nsdi25" / "nsdi25-wang-zixuan.pdf"
            path.parent.mkdir()
            path.write_bytes(PDF)
            legacy = self.paper.to_dict()
            for field in ["translator_id", "translator_last_updated", "zotero_item"]:
                legacy.pop(field)
            legacy["title"] = "Title assembled by the earlier parser"
            state = {"version": 1, "events": {"nsdi25": [{"url": URL, "title": self.paper.title}]},
                     "papers": {URL: {"status": "downloaded", "paper": legacy,
                                      "download": {"path": str(path), "url": PDF_URL, **pdf_integrity(path)}}}}
            state_path = Path(output) / "state.json"
            state_path.write_text(json.dumps(state))
            self.assertEqual(self.run_cli(["download", "--event", "nsdi25", "--output", output]), 0)
            self.client.get_paper.assert_called_once_with(URL)
            updated = json.loads(state_path.read_text())["papers"][URL]
            self.assertEqual(updated["metadata_engine"], METADATA_ENGINE)
            self.assertEqual(updated["paper"]["zotero_item"], self.paper.zotero_item)
            self.assertEqual(updated["paper"]["title"], self.paper.title)
            self.assertEqual(updated["download"]["status"], "existing")
            self.assertEqual(path.read_bytes(), PDF)

    def test_failure_is_saved_and_next_paper_still_downloads(self):
        self.client.get_paper.side_effect = [UsenixError("HTTP 403"), self.other]
        with tempfile.TemporaryDirectory() as output:
            self.assertEqual(self.run_cli(["download", "--event", "nsdi25", "--output", output]), 1)
            state = json.loads((Path(output) / "state.json").read_text())
            self.assertEqual(state["papers"][URL]["status"], "failed")
            self.assertEqual(state["papers"][self.other.url]["status"], "downloaded")

    def test_keynote_does_not_block_limited_batches_or_get_refetched(self):
        keynote = UsenixPresentation(URL.replace("wang-zixuan", "keynote"), "Keynote")
        self.client.list_presentations.return_value.insert(0, keynote)
        def resolve(url):
            if url == keynote.url:
                raise NotUsenixPaper("No paper metadata")
            return self.paper if url == URL else self.other
        self.client.get_paper.side_effect = resolve
        with tempfile.TemporaryDirectory() as output:
            argv = ["download", "--event", "nsdi25", "--output", output, "--limit", "1"]
            self.assertEqual(self.run_cli(argv), 0)
            self.assertEqual(self.run_cli(argv), 0)
            state = json.loads((Path(output) / "state.json").read_text())
            self.assertEqual(state["papers"][keynote.url]["status"], "skipped_talk")
            self.assertEqual(state["papers"][self.other.url]["status"], "downloaded")
            self.assertEqual(self.client.get_paper.call_count, 3)

    def test_failed_paper_does_not_block_another_conference_on_next_batch(self):
        osdi = replace(self.paper, url=URL.replace("nsdi25", "osdi25"), conference="osdi25")
        self.client.list_presentations.side_effect = lambda event: [UsenixPresentation(osdi.url, osdi.title)] if event == "osdi25" else [UsenixPresentation(self.other.url, self.other.title)]
        def resolve(url):
            if url == osdi.url:
                raise UsenixError("HTTP 403")
            return self.other
        self.client.get_paper.side_effect = resolve
        with tempfile.TemporaryDirectory() as output:
            argv = ["download", "--event", "osdi25", "--event", "nsdi25", "--output", output, "--limit", "1"]
            self.assertEqual(self.run_cli(argv), 1)
            self.assertEqual(self.run_cli(argv), 0)
            state = json.loads((Path(output) / "state.json").read_text())
            self.assertEqual(state["papers"][osdi.url]["status"], "failed")
            self.assertEqual(state["papers"][self.other.url]["status"], "downloaded")


if __name__ == "__main__":
    unittest.main()
