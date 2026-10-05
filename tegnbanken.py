"""Tegnbanken.no (minetegn.no / Statped) client.

The Tegnbanken web app is backed by a PHP/XML endpoint that returns all ~11 152
sign records.  This module downloads and caches that XML, then supports
client-side search and image download.

Image types available per record:
  ``foto``    – strektegning (line drawing), often the clearest sign illustration
  ``la_hend`` – hand/body sign illustration

License: Sign illustrations by Statped / tegnbanken.no.
         CC BY-NC-ND 4.0 — free for non-commercial use with attribution.
         See https://creativecommons.org/licenses/by-nc-nd/4.0/
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

_DATA_URL = "https://www.minetegn.no/Tegnbanken-2016/js/data.php"
_FOTO_BASE = "https://www.minetegn.no/Tegnbanken-2016/data/tegn_foto/"
_LAHEND_BASE = "https://www.minetegn.no/Tegnbanken-2016/data/hendene/"
_TIMEOUT = 15  # seconds per request

# Local disk cache – lives in user's home dir, max 7 days old before refresh
_CACHE_DIR = Path.home() / ".cache" / "aacdeck" / "tegnbanken"
_CACHE_FILE = _CACHE_DIR / "data.xml"
_CACHE_MAX_AGE_DAYS = 7

# In-process record cache (populated once per process lifetime)
_records: Optional[list[dict]] = None
_records_lock = threading.Lock()


class SearchError(Exception):
    """Raised when records can't be loaded due to a network error with no cache.

    Distinct from a successful search that simply returned no matches (an empty
    list), so callers can tell "offline" apart from "nothing found".
    """


# ── Internal helpers ───────────────────────────────────────────────────────────


def _fetch_xml() -> bytes:
    req = urllib.request.Request(
        _DATA_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (aacdeck)",
            "Accept": "text/xml, application/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def _load_records() -> list[dict]:
    """Return all sign records, refreshing the disk cache if stale.

    Thread-safe: concurrent callers block until the first fetch completes.
    A corrupt cache file is deleted and re-fetched automatically.
    """
    global _records
    with _records_lock:
        if _records is not None:
            return _records

        # Try disk cache
        raw: Optional[bytes] = None
        if _CACHE_FILE.exists():
            age_days = (time.time() - _CACHE_FILE.stat().st_mtime) / 86400
            if age_days < _CACHE_MAX_AGE_DAYS:
                raw = _CACHE_FILE.read_bytes()

        if raw is None:
            try:
                raw = _fetch_xml()
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _CACHE_FILE.write_bytes(raw)
            except Exception as exc:
                # Network unavailable – use stale cache if present
                if _CACHE_FILE.exists():
                    raw = _CACHE_FILE.read_bytes()
                else:
                    raise SearchError(
                        "Could not reach tegnbanken.no and no local cache exists."
                    ) from exc

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as parse_exc:
            # Cache is corrupt; delete it and attempt a fresh fetch
            if _CACHE_FILE.exists():
                _CACHE_FILE.unlink(missing_ok=True)
            try:
                raw = _fetch_xml()
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                _CACHE_FILE.write_bytes(raw)
                root = ET.fromstring(raw)
            except Exception as exc:
                raise SearchError(
                    "tegnbanken.no data was corrupt and could not be refreshed."
                ) from (exc if not isinstance(exc, ET.ParseError) else parse_exc)

        _records = []
        for tegn in root.findall("tegn"):
            word = (tegn.text or "").strip()
            if not word:
                continue
            _records.append(
                {
                    "word": word,
                    "foto": tegn.get("foto", ""),
                    "la_hend": tegn.get("la_hend", ""),
                }
            )
        return _records


# ── Public API ─────────────────────────────────────────────────────────────────


def search(query: str, limit: int = 40) -> list[dict]:
    """Search sign records by substring match on the word text.

    Results are sorted so that words whose display name *starts with* the query
    come first (prefix-match boost), then remaining substring matches.

    Each result dict::

        {"word": str, "foto": str, "la_hend": str}

    Empty strings for ``foto``/``la_hend`` mean that image type is unavailable.
    Raises :exc:`SearchError` if records can't be loaded (offline, no cache).
    """
    records = _load_records()

    q = query.lower()
    matches = [r for r in records if q in r["word"].lower()]
    matches.sort(key=lambda r: (not r["word"].lower().startswith(q), r["word"].lower()))
    return matches[:limit]


def fetch_image(filename: str, image_type: str = "foto") -> bytes:
    """Download and return raw image bytes for a sign.

    Parameters
    ----------
    filename:
        Value of the ``foto`` or ``la_hend`` XML attribute (e.g. ``"bade.jpg"``).
    image_type:
        ``"foto"`` → strektegning base URL;
        ``"la_hend"`` → hand-sign base URL.

    Raises :exc:`urllib.error.URLError` or :exc:`OSError` on failure.
    """
    base = _LAHEND_BASE if image_type == "la_hend" else _FOTO_BASE
    url = base + filename
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (aacdeck)"},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def invalidate_cache() -> None:
    """Delete the disk cache so it is refreshed on the next search."""
    global _records
    with _records_lock:
        _records = None
        if _CACHE_FILE.exists():
            _CACHE_FILE.unlink(missing_ok=True)


# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "bade"
    print(f"Searching for: {query!r}")
    try:
        results = search(query, limit=10)
    except SearchError as exc:
        sys.exit(f"Search failed: {exc}")
    if not results:
        print("No results.")
    for r in results:
        has_foto = "✓ foto" if r["foto"] else "  ----"
        has_lahend = "✓ la_hend" if r["la_hend"] else "  ----"
        print(f"  {r['word']:<30}  {has_foto}  {has_lahend}")

    if results:
        first = results[0]
        filename = first["foto"] or first["la_hend"]
        itype = "foto" if first["foto"] else "la_hend"
        if filename:
            print(
                f"\nFetching image for '{first['word']}' ({itype}/{filename})…", end=" "
            )
            data = fetch_image(filename, itype)
            print(f"{len(data)} bytes OK")
