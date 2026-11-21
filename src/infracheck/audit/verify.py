"""Verify auditor citations without treating inaccessible pages as evidence.

A citation is `verified` only when its page names the object *and* places it:
the object's city appears where the page talks about it, and an address the
page gives there is the object's street and house.  The name alone is not
enough -- the auditor has cited a «Вкусно как Дома» in Moscow and a «Калинка»
in Lodeynoye Pole for Arkhangelsk objects -- so a page naming the object at
another address or in another city `contradicted`s the label, and a page naming
it with no place at all is `weak`.

On top of the per-page outcome, `verify_label` grades the label's strength
(protocol §2, rule of 2026-11-21): `exists = yes` and `open = open` survive only
on a verified page carrying the evidence date the auditor quotes (2025 or
later), or on a verified, live card of a primary directory.  Mirrors of the
directories -- the aggregators -- are never enough on their own.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
from licmatch import DEFAULTS, address_score, fold, home_city_re, parse_address_fields, parse_address_line

from infracheck.core.names import NAME_MIN, _keys, similar

_AGGREGATORS = ("zoon", "spravker", "restoclub", "tripadvisor", "flamp", "yell", "orgpage", "list-org", "rusprofile")
# Primary directories and the path of an organisation's own card in each: a
# card there is the directory itself, so a live one may carry a label without a
# dated quote.  A search, a building or a reviewer's page is not a card.
PRIMARY_CARDS = {"yandex": "/org/", "2gis": "/firm/", "vk": "/", "ok": "/"}
# The earliest evidence date that makes a label current (protocol §2).
FRESH_FROM = "2025-01-01"
# How much body text around a name hit counts as "where the page talks about
# it": lists write the name and then its address, so the window reaches 80
# characters forward and only 40 back -- one entry, not its neighbours'.
WINDOW_BEFORE, WINDOW_AFTER = 40, 80
MIN_VISIBLE = 500

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_HEAD_META = {"description", "og:title", "og:description"}
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-z:-]+)\s*=\s*"([^"]*)"', re.I)

# Street-type words as pages write them, each folded onto a TYPE_TABLE key so
# that `parse_address_fields` reads the type the way the register adapter does.
# Abbreviations need their dot: a bare "ш" or "пл" is too common to anchor on.
_STREET_TYPES = (
    (r"улиц[а-я]*|ул\.", "ул"),
    (r"проспект[а-я]*|просп\.|пр-кт|пр-т|пр\.", "пр"),
    (r"переул[а-я]*|пер\.", "пер"),
    (r"набережн[а-я]*|наб\.", "наб"),
    (r"шоссе|ш\.", "ш"),
    (r"площад[а-я]*|пл\.", "пл"),
    (r"проезд[а-я]*", "проезд"),
    (r"бульвар[а-я]*|б-р", "бульвар"),
    (r"тракт[а-я]*", "тракт"),
    (r"микрорайон[а-я]*|мкр\.?", "мкр"),
)
_TYPE_ALT = "|".join(f"(?:{pattern})" for pattern, _ in _STREET_TYPES)
_WORD = r"[0-9a-zа-я][0-9a-zа-я-]*"
# A name written before its type is an adjective, perhaps after a number
# ("Окружное шоссе", "Ленинградский проспект"); anything wider swallows the
# words in front ("по адресу Никольский проспект").
_ADJECTIVE = r"(?:\d+-?[а-я]*\s+)?[а-я-]+(?:ий|ый|ой|ая|яя|ое|ее)"
_HOUSE = (
    r"(?:д(?:ом)?\.?\s*)?(?P<house>\d{1,3}[а-я]?(?![0-9])"
    r"(?:\s*,?\s*(?:корпус|корп\.?|к\.?|строение|стр\.?|с\.?)\s*\d+)?)(?![0-9а-я-])"
)
# "улица Серафимовича, 34" / "Окружное шоссе, 13" / "ул. 50-летия Октября, д. 25".
_TYPED_ADDRESS = re.compile(
    rf"(?<![0-9a-zа-я-])(?:(?P<t1>{_TYPE_ALT})\s*(?P<n1>{_WORD}(?:\s+{_WORD}){{0,2}}?)"
    rf"|(?P<n2>{_ADJECTIVE})\s+(?P<t2>{_TYPE_ALT}))(?:\s*,\s*|\s+){_HOUSE}"
)
# "Калинина, 17 к1": with no type word it may confirm an address but is too
# loose to contradict one.
_LOOSE_ADDRESS = re.compile(rf"(?<![0-9a-zа-я-])(?P<n>{_WORD}(?:\s+{_WORD})?)\s*,\s*{_HOUSE}")
# "г. Москва", "город Лодейное Поле" -- never "2025 г. открылся".
_OTHER_CITY = re.compile(r"(?<![0-9])(?<![0-9]\s)(?:\bг\.\s*|\bгород\s+)(?P<city>[а-я][а-я-]{2,})")
_OTHER_REGION = re.compile(r"\b(?P<region>[а-я-]{3,}ая)\s+(?:обл\.|область|области)")
_CLOSED = re.compile(
    r"больше не работает|закрыт[аоы]? навсегда|организация закрыта|компания закрыта"
    r"|прекратил[аио]? (?:работу|деятельность)"
)
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)")
_DOT_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d\d)\b")
_WORD_DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(20\d\d)\b")


class _PageParser(HTMLParser):
    """Two views of one page: everything, and the readable body.

    `parts` keeps script data too (the name search has always looked there);
    `body` is what a reader sees.
    """

    def __init__(self):
        super().__init__()
        self.parts = []
        self.body = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        self.parts.append(data)
        if not self._skip:
            self.body.append(data)


def _head(raw: str) -> str:
    """The page's title and descriptions, which on a card name the place.

    Read with a pattern, not the parser: Yandex Maps ships its <head> inside a
    hydration script, where the parser sees only script data.  The first of
    each is the page's own; later ones belong to embedded related cards.
    """
    found = []
    if title := _TITLE.search(raw):
        found.append(title.group(1))
    for key in sorted(_HEAD_META):
        for tag in _META.finditer(raw):
            attrs = dict(_ATTR.findall(tag.group(0)))
            if (attrs.get("name") or attrs.get("property") or "").lower() == key:
                found.append(attrs.get("content", ""))
                break
    return html.unescape(" ".join(found))


@dataclass(frozen=True)
class Page:
    """One cached page, normalised once: an export checks a page many times."""

    visible: int
    text: str
    body: str
    head: str
    dates: frozenset = field(default_factory=frozenset)


@dataclass(frozen=True)
class Check:
    outcome: str
    closed: bool = False
    dates: frozenset = field(default_factory=frozenset)


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


def is_primary(url: str, name: str) -> bool:
    """A card of a primary directory, or the organisation's own site.

    An own site is recognised by its domain reading as the fascia
    ("bristol.ru" for «Бристоль»); any other unknown domain counts as a mirror,
    which is the safe side of the rule.
    """
    kind = source_type(url)
    if kind in PRIMARY_CARDS:
        return PRIMARY_CARDS[kind] in urlparse(url).path
    if kind != "other":
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    labels = host.split(".")
    label = labels[-2] if len(labels) > 1 else labels[0]
    if label.startswith("xn--"):
        try:
            label = label.encode().decode("idna")
        except UnicodeError:
            return False
    return len(label) >= 4 and similar(name, label) >= NAME_MIN


class URLVerifier:
    def __init__(self, cache_dir: Path, session=None, offline=False):
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self.offline = offline
        self.last_request = None
        self._pages = {}

    def fetch(self, url: str) -> str | None:
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["text"]
        if self.offline or urlparse(url).scheme not in {"http", "https"}:
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
        if len(text) >= MIN_VISIBLE:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "text": response.text}, ensure_ascii=False), encoding="utf-8")
        return response.text

    def page(self, url: str) -> Page | None:
        if url not in self._pages:
            raw = self.fetch(url)
            self._pages[url] = None if raw is None else _parse(raw)
        return self._pages[url]

    def verify(self, url: str, name: str, quote: str, city: str = "", address: str = "") -> str:
        return self.check(url, name, quote, city, address).outcome

    def check(self, url: str, name: str, quote: str, city: str = "", address: str = "") -> Check:
        page = self.page(url)
        if page is None or page.visible < MIN_VISIBLE:
            return Check("unreachable")
        candidates = [name, *_keys(name)]
        candidates.extend(re.findall(r'[«»"“”]([^«»"“”]+)[«»"“”]', name))
        fragments = re.split(r"[«»\"“”…;]+", quote)
        candidates.extend(fragment for fragment in fragments if len(_normal(fragment)) >= 8)
        needles = list(dict.fromkeys(normal for value in candidates if (normal := _normal(value))))
        if not any(needle in page.text for needle in needles):
            return Check("contradicted", dates=page.dates)
        location, mentions = _location(page, needles)
        return Check(_place(location, mentions, city, address), bool(_CLOSED.search(location)), page.dates)


def _parse(raw: str) -> Page:
    parser = _PageParser()
    parser.feed(raw)
    text = " ".join(parser.parts)
    return Page(len(text), _normal(text), _normal(" ".join(parser.body)), _normal(_head(raw)), _dates(text))


def _visible_text(page: str) -> str:
    parser = _PageParser()
    parser.feed(page)
    return " ".join(parser.parts)


def _normal(value: str) -> str:
    return re.sub(r"\s+", " ", fold(value).replace("ё", "е")).strip()


def _location(page: Page, needles: list[str]) -> tuple[str, str]:
    """(where the page places the object, every passage that mentions it).

    A head naming the object makes the page a card of it, and its title is the
    place.  On any other page (a street list, an address page, a review) the
    place is the head plus the body around each mention of the object.  The
    mentions are kept apart: a chain's card for one branch may still list the
    object's own address among the others.
    """
    windows = [page.head]
    for needle in needles:
        for hit in re.finditer(re.escape(needle), page.body):
            windows.append(page.body[max(0, hit.start() - WINDOW_BEFORE) : hit.end() + WINDOW_AFTER])
    mentions = " … ".join(windows)
    if any(needle in page.head for needle in needles):
        return page.head, mentions
    return mentions, mentions


def city_pattern(city: str) -> re.Pattern | None:
    """The city in any case form: Архангельск, в Архангельске, Архангельская обл."""
    stem = _normal(city)
    return re.compile(rf"(?<![а-я]){re.escape(stem)}[а-я]*") if stem else None


def object_toponyms(address: str, city: str) -> list:
    """The object's address as Toponyms; a double address yields both halves.

    "Мельникова, 9/Новодвинск, 50-летия Октября, 25" is two doors; a slash
    before a digit is a house fraction ("45/3"), not a second address.  A half
    without both a street and a house cannot be compared and is dropped.
    """
    home = home_city_re(*([city] if city else []))
    parts = re.split(r"/(?=\s*[^\d\s])", str(address or ""))
    return [t for t in (parse_address_line(part, home) for part in parts) if t.street and t.house]


def page_addresses(text: str, city: str) -> tuple[list, list]:
    """(typed, loose) addresses written in `text`, as Toponyms."""
    home = home_city_re(*([city] if city else []))
    typed = [
        parse_address_fields(f"{_type_key(m.group('t1') or m.group('t2'))} {m.group('n1') or m.group('n2')}",
                             m.group("house"), home)
        for m in _TYPED_ADDRESS.finditer(text)
    ]
    loose = [parse_address_fields(m.group("n"), m.group("house"), home) for m in _LOOSE_ADDRESS.finditer(text)]
    return [t for t in typed if t.street], [t for t in loose if t.street]


def _type_key(kind: str) -> str:
    return next(key for pattern, key in _STREET_TYPES if re.fullmatch(pattern, kind))


def _place(location: str, mentions: str, city: str, address: str) -> str:
    """verified / contradicted / weak for a page that does name the object."""
    pattern = city_pattern(city)
    city_seen = bool(pattern and pattern.search(location))
    own = object_toponyms(address, city)
    if own:
        typed, loose = page_addresses(mentions, city)
        if any(address_score(mine, theirs) >= DEFAULTS.addr_min for mine in own for theirs in typed + loose):
            return "verified" if pattern and pattern.search(mentions) else "weak"
        if page_addresses(location, city)[0]:
            return "contradicted"
    if city_seen:
        return "verified"
    stem = _normal(city)
    if any(not m.group("city").startswith(stem) for m in _OTHER_CITY.finditer(location)) or any(
        not m.group("region").startswith("архангельск") for m in _OTHER_REGION.finditer(location)
    ):
        return "contradicted"
    return "weak"


def _dates(text: str) -> frozenset:
    """Every full date written on the page, as YYYY-MM-DD."""
    folded = fold(text)
    found = {f"{y}-{m}-{d}" for y, m, d in _ISO_DATE.findall(folded)}
    found |= {f"{y}-{int(m):02d}-{int(d):02d}" for d, m, y in _DOT_DATE.findall(folded)}
    found |= {f"{y}-{_MONTHS[m]:02d}-{int(d):02d}" for d, m, y in _WORD_DATE.findall(folded)}
    return frozenset(found)


def evidence_day(value) -> str:
    """The auditor's evidence date as YYYY-MM or YYYY-MM-DD; "" if coarser.

    A bare year is not a date: every page carries the current one in a footer.
    """
    s = str(value or "").strip()
    if m := re.match(r"^(20\d\d)-(\d\d)(?:-(\d\d))?", s):
        return "-".join(part for part in m.groups() if part)
    if m := re.match(r"^(\d{1,2})\.(\d{1,2})\.(20\d\d)", s):
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def label_strength(label: dict, name: str, checks: list[tuple[str, Check]]) -> str:
    """dated / live_card / weak, from the verified citations only.

    dated: a verified page carries the auditor's evidence date, which is
    2025-01-01 or later and is not the audit day (an auditor writing the day it
    looked has not dated the evidence).  live_card: a verified card of a primary
    directory, or the organisation's own site, with no closure mark where it
    names the object.
    """
    verified = [(url, check) for url, check in checks if check.outcome == "verified"]
    day = evidence_day(label.get("evidence_date"))
    audit_day = str(label.get("audit_date") or "")[:10]
    fresh = bool(day) and day >= FRESH_FROM[: len(day)] and day != audit_day
    if fresh and any(date.startswith(day) for _, check in verified for date in check.dates):
        return "dated"
    if any(is_primary(url, name) and not check.closed for url, check in verified):
        return "live_card"
    return "weak"


def verify_label(label: dict, name: str, verifier: URLVerifier, city: str = "", address: str = "") -> dict:
    """Check every citation, then grade the label; originals stay in *_raw and notes."""
    result = dict(label)
    urls = result.get("urls") or []
    if isinstance(urls, str):
        urls = [value.strip() for value in urls.split(";") if value.strip()]
    result["url_types"] = [source_type(url) for url in urls]
    checks = [(url, verifier.check(url, name, result.get("quote", ""), city, address)) for url in urls]
    outcomes = [check.outcome for _, check in checks]
    result["url_outcomes"] = ";".join(outcomes)
    result["n_verified"] = outcomes.count("verified")
    result["n_contradicted"] = outcomes.count("contradicted")
    result["n_weak"] = outcomes.count("weak")
    result["n_unreachable"] = outcomes.count("unreachable")
    result["verified_url_types"] = [source_type(url) for url, outcome in zip(urls, outcomes, strict=True)
                                    if outcome == "verified"]
    result["exists_raw"], result["open_raw"] = result.get("exists"), result.get("open")
    if "contradicted" in outcomes and "verified" not in outcomes:
        original = {key: result.get(key) for key in ("exists", "open", "category_ok", "is_vending")}
        result["notes"] = (
            str(result.get("notes") or "")
            + " Не проверены источники; исходный вердикт: "
            + json.dumps(original, ensure_ascii=False)
        ).strip()
        result.update({key: "unclear" for key in original})
    result["label_strength"] = label_strength(result, name, checks)
    lowered = {key: value for key, value in (("exists", "yes"), ("open", "open")) if result.get(key) == value}
    if result["label_strength"] == "weak" and lowered:
        result["notes"] = (
            str(result.get("notes") or "")
            + " Слабая метка: нет проверенного свидетельства с датой от 2025-01-01 и нет живой карточки"
            " первичного справочника; понижено до unclear: "
            + json.dumps(lowered, ensure_ascii=False)
        ).strip()
        result.update({key: "unclear" for key in lowered})
    return result
