"""Verify auditor citations without treating inaccessible pages as evidence."""

from __future__ import annotations

import hashlib
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
from licmatch import fold

from infracheck.core.names import _keys

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
            response = self.session.get(url, timeout=15, headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:132.0) "
                    "Gecko/20100101 Firefox/132.0"
                ),
                "Accept-Language": "ru,en;q=0.8",
            })
            response.raise_for_status()
        except requests.RequestException:
            return None
        text = _visible_text(response.text)
        if len(text) >= 500:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "text": response.text}, ensure_ascii=False), encoding="utf-8")
        return response.text

    def verify(self, url: str, name: str, quote: str) -> str:
        page = self.fetch(url)
        if page is None:
            return "unreachable"
        visible = _visible_text(page)
        if len(visible) < 500:
            return "unreachable"
        page_normal = _normal(visible)
        candidates = [name, *_keys(name)]
        candidates.extend(re.findall(r'[«»"“”]([^«»"“”]+)[«»"“”]', name))
        fragments = re.split(r"[«»\"“”…;]+", quote)
        candidates.extend(fragment for fragment in fragments if len(_normal(fragment)) >= 8)
        if any((normal := _normal(value)) and normal in page_normal for value in candidates):
            return "verified"
        return "contradicted"


def _visible_text(page: str) -> str:
    parser = _TextParser()
    parser.feed(page)
    return " ".join(parser.parts)


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", fold(value).replace("ё", "е")).strip()


def verify_label(label: dict, name: str, verifier: URLVerifier) -> dict:
    """Keep the original verdict in notes when no citation verifies."""
    result = dict(label)
    urls = result.get("urls") or []
    if isinstance(urls, str):
        urls = [value.strip() for value in urls.split(";") if value.strip()]
    result["url_types"] = [source_type(url) for url in urls]
    outcomes = [verifier.verify(url, name, result.get("quote", "")) for url in urls]
    result["url_outcomes"] = ";".join(outcomes)
    result["n_verified"] = outcomes.count("verified")
    result["n_contradicted"] = outcomes.count("contradicted")
    result["n_unreachable"] = outcomes.count("unreachable")
    result["verified_url_types"] = [source_type(url) for url, outcome in zip(urls, outcomes, strict=True)
                                    if outcome == "verified"]
    if "contradicted" in outcomes and "verified" not in outcomes:
        original = {key: result.get(key) for key in ("exists", "open", "category_ok", "is_vending")}
        result["notes"] = (
            str(result.get("notes") or "")
            + " Не проверены источники; исходный вердикт: "
            + json.dumps(original, ensure_ascii=False)
        ).strip()
        result.update({key: "unclear" for key in original})
    return result
