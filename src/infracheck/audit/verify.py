"""Verify auditor citations without treating inaccessible pages as evidence."""

from __future__ import annotations

import hashlib
import json
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
from licmatch import fold

_AGGREGATORS = ("zoon", "spravker", "restoclub", "tripadvisor", "flamp", "yell", "orgpage", "list-org", "rusprofile")


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def source_type(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    domain = ".".join(host.split(".")[-2:])
    label = domain.split(".")[0]
    if label in {"yandex", "ya"}:
        return "yandex"
    if label == "2gis":
        return "2gis"
    if label == "vk":
        return "vk"
    if label == "ok":
        return "ok"
    if label in {"openstreetmap", "osm"}:
        return "osm"
    if label in {"fsrar", "egais"}:
        return "fsrar/egais"
    if label in _AGGREGATORS:
        return "aggregator"
    return "other"


class URLVerifier:
    def __init__(self, cache_dir: Path, session=None):
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self.last_request = None

    def fetch(self, url: str) -> str | None:
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        if urlparse(url).scheme not in {"http", "https"}:
            return None
        if self.last_request is not None:
            time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            response = self.session.get(
                url, timeout=15, headers={"User-Agent": "InfraCheckAudit/1.0 (research verification)"}
            )
            response.raise_for_status()
        except requests.RequestException:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"url": url, "text": response.text}, ensure_ascii=False), encoding="utf-8")
        return response.text

    def verify(self, url: str, name: str, quote: str) -> bool:
        page = self.fetch(url)
        if not page:
            return False
        parser = _TextParser()
        parser.feed(page)
        page_normal = fold(" ".join(parser.parts))
        return any(fold(value) and fold(value) in page_normal for value in (name, quote) if value)


def verify_label(label: dict, name: str, verifier: URLVerifier) -> dict:
    """Keep the original verdict in notes when no citation verifies."""
    result = dict(label)
    urls = result.get("urls") or []
    if isinstance(urls, str):
        urls = [value.strip() for value in urls.split(";") if value.strip()]
    result["url_types"] = [source_type(url) for url in urls]
    verified = [verifier.verify(url, name, result.get("quote", "")) for url in urls]
    if not any(verified):
        original = {key: result.get(key) for key in ("exists", "open", "category_ok", "is_vending")}
        result["notes"] = (
            str(result.get("notes") or "")
            + " Не проверены источники; исходный вердикт: "
            + json.dumps(original, ensure_ascii=False)
        ).strip()
        result.update({key: "unclear" for key in original})
    return result
