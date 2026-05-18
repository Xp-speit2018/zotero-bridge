"""DBLP venue naming utilities.

Maps Zotero item metadata to DBLP-style collection names such as
``issta2023``, ``asplos2025-1``, ``icse2024``, etc.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".dblp_cache.json")

# Hand-curated fallback mappings (normalized key -> DBLP abbrev).
# These are used when the DBLP API is unreachable or returns no hits.
_COMMON_VENUES: dict[str, str] = {
    "aaai": "aaai",
    "acl": "acl",
    "ase": "ase",
    "asplos": "asplos",
    "atc": "atc",
    "ccs": "ccs",
    "chi": "chi",
    "cidr": "cidr",
    "coco": "coco",
    "coling": "coling",
    "crypto": "crypto",
    "cscw": "cscw",
    "cvpr": "cvpr",
    "dac": "dac",
    "date": "date",
    "eccv": "eccv",
    "ecoop": "ecoop",
    "eurocrypt": "eurocrypt",
    "eurosys": "eurosys",
    "fast": "fast",
    "fccm": "fccm",
    "fse": "fse",
    "hawaii international conference on system sciences": "hicss",
    "hicss": "hicss",
    "hotnets": "hotnets",
    "hpca": "hpca",
    "iccad": "iccad",
    "iccad/iccad": "iccad",
    "icde": "icde",
    "icdm": "icdm",
    "iclr": "iclr",
    "icml": "icml",
    "icse": "icse",
    "ics": "ics",
    "icse/sigsoft fse": "fse",
    "ijcai": "ijcai",
    "infocom": "infocom",
    "ipdps": "ipdps",
    "isca": "isca",
    "issta": "issta",
    "kdd": "kdd",
    "lcpc": "lcpc",
    "micro": "micro",
    "mobicom": "mobicom",
    "mobisys": "mobisys",
    "ndss": "ndss",
    "neurips": "neurips",
    "neural information processing systems": "neurips",
    "nsdi": "nsdi",
    "oopsla": "oopsla",
    "osdi": "osdi",
    "pact": "pact",
    "pldi": "pldi",
    "podc": "podc",
    "podc/podc": "podc",
    "pact": "pact",
    "ppopp": "ppopp",
    "sc": "sc",
    "sigcomm": "sigcomm",
    "sigmetrics": "sigmetrics",
    "sigmod": "sigmod",
    "sosp": "sosp",
    "sp": "sp",
    "uss": "uss",
    "ussenix security": "uss",
    "usenix security symposium": "uss",
    "vldb": "vldb",
    "wine": "wine",
    "www": "www",
}


# ------------------------------------------------------------------ #
# Cache helpers
# ------------------------------------------------------------------ #

def _load_cache() -> dict[str, Any]:
    if os.path.exists(_CACHE_PATH):
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    with open(_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------ #
# DBLP API helpers
# ------------------------------------------------------------------ #

def _query_dblp_venue(raw_name: str) -> str | None:
    """Search DBLP venue API and return the abbreviation (e.g. ``'issta'``)."""
    cache = _load_cache()
    key = raw_name.lower().strip()
    if key in cache:
        return cache[key]

    try:
        resp = requests.get(
            "https://dblp.org/search/venue/api",
            params={"q": raw_name, "format": "json", "h": 5},
            headers={"User-Agent": "Mozilla/5.0 (zotero-bridge-sdk)"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        for hit in hits:
            url = hit.get("info", {}).get("url", "")
            # URL: https://dblp.org/db/conf/issta/
            m = re.search(r"/db/(conf|journals)/([^/]+)/", url)
            if m:
                abbrev = m.group(2)
                cache[key] = abbrev
                _save_cache(cache)
                return abbrev
    except Exception:
        pass

    return None


def _extract_acronym(text: str) -> str | None:
    """Try to pull a conference acronym out of free-form text.

    Heuristics (applied in order):
    1. ``SIG[A-Z]+``  (e.g. SIGSOFT, SIGPLAN)
    2. ``[A-Z][a-z]+[A-Z][A-Za-z]*`` (e.g. EuroSys, HotNets, CoNEXT)
    3. Leading all-caps block (e.g. ISSTA, ASPLOS, ICSE)
    """
    if not text:
        return None

    # Remove year markers like "'23" or "(2023)"
    cleaned = re.sub(r"\s*'\d{2,4}\b", "", text)
    cleaned = re.sub(r"\s*\(\d{4}\)", "", cleaned)

    # 1. SIG- names
    m = re.search(r"\b(SIG[A-Z]+)\b", cleaned)
    if m:
        return m.group(1).lower()

    # 2. CamelCase acronyms (EuroSys, HotNets, CoNEXT, etc.)
    m = re.search(r"\b([A-Z][a-z]+[A-Z][A-Za-z]*)\b", cleaned)
    if m:
        return m.group(1).lower()

    # 3. Leading all-caps block
    m = re.match(r"([A-Z][A-Z&/]+)\b", cleaned)
    if m:
        return m.group(1).lower().replace("&", "").replace("/", "")

    return None


def _get_abbreviation(venue_name: str) -> str:
    """Map a free-form venue name to a DBLP-style abbreviation."""
    normalized = venue_name.lower().strip()

    # 1. Exact common-venue match
    if normalized in _COMMON_VENUES:
        return _COMMON_VENUES[normalized]

    # 2. Substring common-venue match
    for key, abbrev in _COMMON_VENUES.items():
        if key in normalized or normalized in key:
            return abbrev

    # 3. Query DBLP API
    dblp_abbrev = _query_dblp_venue(venue_name)
    if dblp_abbrev:
        return dblp_abbrev

    # 4. Heuristic acronym extraction
    acronym = _extract_acronym(venue_name)
    if acronym:
        return acronym

    # 5. Brutal fallback: alphanum only, truncated
    return re.sub(r"[^a-z0-9]", "", normalized)[:20]


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #

def extract_year(date_str: str | None) -> str | None:
    """Pull the first 4-digit year (19xx or 20xx) from a date string."""
    if not date_str:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", str(date_str))
    return m.group(0) if m else None


def normalize_venue_name(
    item: dict[str, Any],
    default_abbrev: str | None = None,
) -> str:
    """Convert Zotero item metadata to a DBLP-style collection name.

    Args:
        item: Dict returned by ``get_item()`` or ``add_by_identifier()``.
        default_abbrev: Optional fallback abbreviation if auto-detection fails.

    Returns:
        A string like ``issta2023``, ``asplos2025-1``, ``icse2024``, etc.
    """
    # --- year ---
    year = extract_year(item.get("date"))
    if not year:
        # Try to extract from conferenceName (e.g. "ISSTA '23")
        cn = item.get("conferenceName", "")
        m = re.search(r"'?(\d{2,4})\b", cn)
        if m:
            y = m.group(1)
            year = "20" + y if len(y) == 2 else y

    # --- venue abbreviation ---
    venue = item.get("conferenceName") or item.get("publicationTitle") or ""
    abbrev = _get_abbreviation(venue) if venue else (default_abbrev or "unknown")
    if not abbrev:
        abbrev = default_abbrev or "unknown"

    # --- volume suffix ---
    volume = str(item.get("volume") or "").strip()
    if volume.isdigit():
        if int(volume) > 1:
            return f"{abbrev}{year}-{volume}"
        # Volume 1: some venues use "-1" (e.g. ASPLOS), some omit it.
        # We conservatively include "-1" when the translator provided a
        # volume number, because that usually means multi-volume proceedings.
        return f"{abbrev}{year}-1"

    return f"{abbrev}{year}"


def get_dblp_url(venue_name: str, year: str | None = None, volume: str | None = None) -> str | None:
    """Return a DBLP URL for a venue + year + volume, or ``None``.

    Examples::

        >>> get_dblp_url("ASPLOS", "2025", "1")
        'https://dblp.org/db/conf/asplos/asplos2025-1.html'
    """
    abbrev = _get_abbreviation(venue_name)
    if not year:
        return f"https://dblp.org/db/conf/{abbrev}/index.html"
    if volume and volume.isdigit():
        return f"https://dblp.org/db/conf/{abbrev}/{abbrev}{year}-{volume}.html"
    return f"https://dblp.org/db/conf/{abbrev}/{abbrev}{year}.html"
