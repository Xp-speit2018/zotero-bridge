"""USENIX discovery, native Zotero translation and verified PDF downloads.

Uses the modern /conference/<event>/technical-sessions site, not the
attendee-only ZIP archives or the legacy static.usenix.org/events site.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests


BASE_URL = "https://www.usenix.org"
USER_AGENT = "zotero-bridge-usenix/1.0"
MIN_INTERVAL = 10.0
USENIX_TRANSLATOR_ID = "b97462fa-f20b-4a1e-8a73-3a434a81518b"
METADATA_ENGINE = "zotero-usenix-translator"
_EVENT = re.compile(r"[a-z][a-z0-9-]*\d{2}")
_PRESENTATION = re.compile(r"/conference/([a-z][a-z0-9-]*\d{2})/(?:technical-sessions/)?presentation/[^/]+/?$")


class UsenixError(RuntimeError):
    """A page, metadata record or PDF could not be retrieved safely."""


class NotUsenixPaper(UsenixError):
    """A presentation is a talk without scholarly paper metadata."""


def conference_id(venue: str, year: int | None = None) -> str:
    """Accept either ``osdi25`` or ``OSDI, 2025`` (similarly for NSDI etc.)."""
    event = venue.strip().lower()
    if year is not None:
        if not 2000 <= year <= 2099 or not re.fullmatch(r"[a-z][a-z-]*", event):
            raise ValueError("Use an event acronym and a year between 2000 and 2099")
        event += f"{year % 100:02d}"
    if not _EVENT.fullmatch(event):
        raise ValueError("Expected an event ID such as osdi25 or nsdi25")
    return event


def canonical_paper_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"usenix.org", "www.usenix.org"}:
        raise ValueError("Expected a public USENIX presentation URL")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("Unexpected credentials or port in USENIX URL")
    if not _PRESENTATION.fullmatch(parsed.path):
        raise ValueError("Expected /conference/<event>/presentation/<speaker>")
    return BASE_URL + parsed.path.rstrip("/")


def is_usenix_paper_url(url: str) -> bool:
    try:
        canonical_paper_url(url)
        return True
    except ValueError:
        return False


def _pdf_url(href: str, page_url: str) -> str | None:
    parsed = urlsplit(urljoin(page_url, href))
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"www.usenix.org", "usenix.org"}
            or not parsed.path.lower().endswith(".pdf")):
        return None
    if not parsed.path.startswith(("/system/files/", "/sites/default/files/")):
        return None
    return urlunsplit(("https", "www.usenix.org", parsed.path, parsed.query, ""))


class _Page(HTMLParser):
    """Read only conference directory links and their heading contexts."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str]]] = []
        self.links: list[dict[str, Any]] = []
        self._anchor: dict[str, Any] | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        context = classes.union(*(c for _, c in self.stack))
        if tag == "a" and values.get("href"):
            self._anchor = {"href": values["href"], "text": "", "classes": context,
                            "heading": any(t in {"h1", "h2", "h3"} for t, _ in self.stack)}
            self.links.append(self._anchor)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, classes))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._anchor = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if any(tag in {"script", "style"} for tag, _ in self.stack):
            return
        if self._anchor is not None:
            self._anchor["text"] += data


@dataclass(frozen=True)
class UsenixPresentation:
    url: str
    title: str


@dataclass(frozen=True)
class UsenixPaper:
    url: str
    conference: str
    title: str
    year: int
    authors: tuple[str, ...]
    proceedings: str
    pdf_url: str | None
    pdf_version: str | None
    bibtex: str = ""
    doi: str = ""
    isbn: str = ""
    pages: str = ""
    publisher: str = "USENIX Association"
    place: str = ""
    translator_id: str = ""
    translator_last_updated: str = ""
    zotero_item: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_sessions(html: str, event: str) -> list[UsenixPresentation]:
    event = conference_id(event)
    page_url = f"{BASE_URL}/conference/{event}/technical-sessions"
    page = _Page(html)
    candidates = [a for a in page.links if a["heading"] and "node-paper" in a["classes"]]
    if not candidates:
        candidates = page.links
    result: dict[str, UsenixPresentation] = {}
    for anchor in candidates:
        url = urljoin(page_url, anchor["href"])
        if not is_usenix_paper_url(url):
            continue
        url = canonical_paper_url(url)
        if _PRESENTATION.fullmatch(urlsplit(url).path)[1] != event:
            continue  # Joint sessions may link to another conference's keynote.
        title = re.sub(r"\s+", " ", anchor["text"]).strip()
        if title:
            result.setdefault(url, UsenixPresentation(url, title))
    if not result:
        raise UsenixError(f"No presentation links found for {event}; event may be unpublished or use the legacy site")
    return list(result.values())


def paper_from_translator(result: dict[str, Any], url: str) -> UsenixPaper:
    """Expose a native translator result through the SDK's paper record.

    The complete Zotero JSON is retained and passed unchanged to ItemSaver.
    Authors here are display labels, never used to reconstruct creators.
    """
    url = canonical_paper_url(url)
    if result.get("status") != "success" or result.get("translatorID") != USENIX_TRANSLATOR_ID:
        raise UsenixError(f"USENIX translator failed at {url}: {result.get('reason', 'unexpected_translator')}: {result.get('error', '')}")
    item = result.get("item") or {}
    proceedings = item.get("proceedingsTitle") or item.get("conferenceName")
    if item.get("itemType") != "conferencePaper" or not item.get("title") or not proceedings:
        raise NotUsenixPaper(f"USENIX translator did not identify a conference paper at {url}")
    year = re.search(r"\b(20\d{2})\b", item.get("date") or "")
    if not year:
        raise UsenixError(f"USENIX translator returned no publication year at {url}")
    authors = tuple(creator.get("name") or " ".join(filter(None, [creator.get("firstName"), creator.get("lastName")]))
                    for creator in item.get("creators", []) if creator.get("creatorType") == "author")
    pdf = next((attachment.get("url") for attachment in item.get("attachments", [])
                if (attachment.get("mimeType") or attachment.get("contentType")) == "application/pdf"), None)
    return UsenixPaper(url=url, conference=_PRESENTATION.fullmatch(urlsplit(url).path)[1],
                       title=item["title"], year=int(year[1]), authors=authors, proceedings=proceedings,
                       pdf_url=pdf, pdf_version="translator" if pdf else None,
                       doi=item.get("DOI", ""), isbn=item.get("ISBN", ""), pages=item.get("pages", ""),
                       publisher=item.get("publisher", ""), place=item.get("place", ""),
                       translator_id=result["translatorID"], translator_last_updated=result.get("translatorLastUpdated", ""),
                       zotero_item=item)


def pdf_integrity(path: Path) -> dict[str, Any]:
    """Reject HTML responses and truncated PDFs; calculate a reproducible digest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise UsenixError(f"File is not a PDF: {path}")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
        size = stream.tell()
        stream.seek(max(0, size - 1024))
        if b"%%EOF" not in stream.read():
            raise UsenixError(f"Truncated PDF (missing EOF): {path}")
    return {"bytes": size, "sha256": digest.hexdigest()}


class UsenixClient:
    """Serial HTTP client, respecting USENIX robots.txt and Retry-After.

    ``download_pdf`` resumes at paper granularity; incomplete files are never
    promoted to completed PDFs. The client is intended for one serial worker.
    """

    def __init__(self, *, interval: float = MIN_INTERVAL, timeout: float = 60,
                 max_attempts: int = 3, session: requests.Session | None = None, bridge: Any | None = None):
        if interval < MIN_INTERVAL or timeout <= 0 or max_attempts < 1:
            raise ValueError("interval must be >= 10, timeout > 0 and max_attempts >= 1")
        self.bridge = bridge
        self.interval = interval
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_request: float | None = None
        self._robots: RobotFileParser | None = None

    def __enter__(self) -> UsenixClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    def _pace(self) -> None:
        if self._last_request is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self._last_request)))
        self._last_request = time.monotonic()

    def _request(self, url: str, *, stream: bool = False) -> requests.Response:
        for attempt in range(self.max_attempts):
            self._pace()
            try:
                response = self.session.get(url, timeout=(min(10, self.timeout), self.timeout), stream=stream)
            except requests.RequestException as error:
                if attempt + 1 == self.max_attempts:
                    raise UsenixError(f"Request failed: {url}: {error}") from error
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                retry_after = response.headers.get("Retry-After", "")
                response.close()
                delay = self.interval * (2 ** attempt)
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
                time.sleep(delay)
                continue
            if not response.ok:
                status = response.status_code
                response.close()
                raise UsenixError(f"HTTP {status}: {url}")
            return response
        raise AssertionError("unreachable")

    def _check_robots(self, url: str) -> None:
        if self._robots is None:
            with self._request(BASE_URL + "/robots.txt") as response:
                robots = RobotFileParser()
                robots.parse(response.text.splitlines())
            self._robots = robots
            self.interval = max(self.interval, robots.crawl_delay(USER_AGENT) or MIN_INTERVAL)
        if not self._robots.can_fetch(USER_AGENT, url):
            raise UsenixError(f"robots.txt disallows {url}")

    def _html(self, url: str) -> str:
        self._check_robots(url)
        with self._request(url) as response:
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise UsenixError(f"Expected an HTML page: {url}")
            return response.text

    def list_presentations(self, venue: str, year: int | None = None) -> list[UsenixPresentation]:
        event = conference_id(venue, year)
        return parse_sessions(self._html(f"{BASE_URL}/conference/{event}/technical-sessions"), event)

    def get_paper(self, url: str) -> UsenixPaper:
        url = canonical_paper_url(url)
        self._check_robots(url)
        self._pace()
        if self.bridge is None:
            from .client import ZoteroBridge
            self.bridge = ZoteroBridge(request_timeout=self.timeout)
        from .client import ZoteroBridgeError
        try:
            translated = self.bridge.translate_usenix_paper(url)
        except ZoteroBridgeError as error:
            raise UsenixError(f"Zotero translator bridge failed: {error}") from error
        return paper_from_translator(translated, url)

    def download_pdf(self, paper: UsenixPaper, output: str | Path, *, expected_sha256: str | None = None,
                     force: bool = False) -> dict[str, Any]:
        if not paper.pdf_url:
            raise UsenixError(f"No public paper PDF at {paper.url}; it may not have been released")
        if _pdf_url(paper.pdf_url, paper.url) != paper.pdf_url:
            raise UsenixError("PDF URL must be a public USENIX file")
        directory = Path(output).expanduser().resolve() / paper.conference
        directory.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w.-]+", "_", paper.title, flags=re.UNICODE).strip("._")[:100] or "paper"
        slug = slug.encode("utf-8")[:180].decode("utf-8", errors="ignore")
        identity = hashlib.sha256(paper.url.encode()).hexdigest()[:10]
        path = directory / f"{identity}_{slug}.pdf"
        if path.exists() and not force:
            try:
                info = pdf_integrity(path)
                if expected_sha256 is None or info["sha256"] == expected_sha256:
                    return {"status": "existing", "path": str(path), "url": paper.pdf_url, **info}
            except UsenixError:
                pass
        self._check_robots(paper.pdf_url)
        temporary: Path | None = None
        try:
            with self._request(paper.pdf_url, stream=True) as response:
                with tempfile.NamedTemporaryFile(dir=directory, prefix=".usenix-", suffix=".part", delete=False) as stream:
                    temporary = Path(stream.name)
                    for chunk in response.iter_content(chunk_size=65536):
                        stream.write(chunk)
                info = pdf_integrity(temporary)
                length = response.headers.get("Content-Length")
                if length and not response.headers.get("Content-Encoding") and info["bytes"] != int(length):
                    raise UsenixError(f"Incomplete PDF response at {paper.pdf_url}")
            temporary.replace(path)
            return {"status": "downloaded", "path": str(path), "url": paper.pdf_url, **info}
        except (requests.RequestException, ValueError) as error:
            raise UsenixError(f"PDF download failed: {paper.pdf_url}: {error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def ingest_paper(self, bridge: Any, paper: UsenixPaper, *, collection_ids: list[int] | None = None,
                     download_pdf: bool = True) -> dict[str, Any]:
        """Create or reuse exact URL/DOI matches, then attach the selected paper.

        The result distinguishes metadata success from PDF availability/failure.
        Repeated calls verify the matching attachment still exists in Zotero.
        """
        if paper.translator_id != USENIX_TRANSLATOR_ID or not paper.zotero_item:
            raise UsenixError("Resolve the paper with Zotero's USENIX translator before saving")
        existing = None
        queries = [(paper.url, "url")]
        if paper.doi:
            queries.append((paper.doi, "DOI"))
        for identifier, kind in queries:
            matches = bridge.lookup(identifier, kind).get("matches") or []
            for match in matches:
                if ((kind == "url" and is_usenix_paper_url(match.get("url") or "")
                     and canonical_paper_url(match["url"]) == paper.url)
                        or (kind == "DOI" and (match.get("DOI") or "").casefold() == paper.doi.casefold())):
                    existing = match
                    break
            if existing:
                break
        if existing:
            result = {"status": "success", "action": "existing", "itemID": existing.get("itemID", existing.get("id")),
                      "key": existing.get("key")}
            for cid in collection_ids or []:
                bridge.add_to_collection(result["itemID"], cid)
        else:
            if download_pdf:
                self._check_robots(paper.pdf_url or paper.url)
                self._pace()
            result = bridge.save_translated_item(paper.zotero_item, collection_ids=collection_ids,
                                                 save_attachments=download_pdf)
            result["action"] = "created" if result.get("status") == "success" else "failed"
        result.update({"method": "usenix-translator", "translatorID": paper.translator_id, "title": paper.title, "url": paper.url, "pdfURL": paper.pdf_url})
        if result.get("status") != "success" or not result.get("itemID"):
            return result
        if not download_pdf:
            result["pdf_status"] = "not_requested"
            return result
        if not paper.pdf_url:
            result["pdf_status"] = "unavailable"
            return result
        attachments = bridge.get_attachments(result["itemID"]) if existing else result.get("attachments", [])
        for attachment in attachments:
            if (attachment.get("contentType") == "application/pdf" and attachment.get("fileExists")
                    and attachment.get("url") == paper.pdf_url):
                result.update({"pdf_status": "existing" if existing else "downloaded", "attachmentID": attachment["id"]})
                return result
        if not existing:
            result["pdf_status"] = "failed"
            return result
        self._check_robots(paper.pdf_url)
        self._pace()
        attached = bridge.attach_file_from_url(result["itemID"], paper.pdf_url)
        result["attachment_result"] = attached
        result["pdf_status"] = "downloaded" if attached.get("status") == "success" else "failed"
        if attached.get("attachmentID"):
            result["attachmentID"] = attached["attachmentID"]
        return result
