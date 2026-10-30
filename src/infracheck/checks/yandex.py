"""Yandex Maps as a source: the contractor's hit re-judged, and our own query.

Two readings of one directory, kept as two checks because they are two
different claims:

* ``yandex_contractor`` -- the contractor's own «Яндекс: найдено».  Its rule is
  city + full address + category, so it confirms an *address*, not an object:
  the name matched in 190 of 407 Arkhangelsk cases, and «Tupperware,
  дистрибьюторный центр» counts as found because «Йоко суши» shares its
  building.  The names Yandex returned are kept in `Яндекс: название`, so the
  hit is re-judged here with the same name rule as our own check, at no cost
  in requests.  A name that matches is strong; a raw «Найдено» with no name
  match is weak (the door has *a* catering business, not this one).
* ``yandex`` -- our own query, judged from the cache.  For every object we issue
  the *same query a person would type* -- name, then address, with the map
  centred on the object's own coordinate -- and read the answer off the page
  Yandex renders server-side: a business card carrying `data-coordinates`, or
  a list of snippets, or neither.  A candidate is accepted only when it stands
  within `Policy.near_m` of the object and its name key matches; everything
  else is recorded and left for the reader to judge.  Yandex marks dead
  organisations "Больше не работает", and that flag is kept apart from both
  "found" and "not found": a closed card asserts the opposite of existence.

What the cache holds is the page's *answer*, not the verdict: every
organisation Yandex put on the page, with its name, address and coordinate, one
JSONL line per object keyed by `site_key`.  Judging happens on the way out, so
a threshold can be moved and the whole list re-decided without a single
request; a row is re-read only by deleting its line.  The cache is a
*snapshot*: Yandex is a living directory, and every row carries the date it
was read.

Yandex is one source however reached; this module emits evidence only and
decides nothing.  Which objects are worth querying is a category decision --
the network part (`fetch` command) takes an id list.

    python -m infracheck.checks.yandex fetch --objects OBJ.parquet --cache CACHE.jsonl \\
        [--only-ids FILE] [--limit N] [--sleep MIN MAX] [--refresh]

Ported from `arkhinfra.v4_yandex` and `arkhinfra.final_confirm`
(`contractor_yandex`, `own_yandex`, `_yandex_reason`, `_against`,
`_yandex_rubric`) and `scripts/confirm_final.py` (`query_yandex`).
"""
from __future__ import annotations

import argparse
import html as _html
import http.client
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from infracheck.core import entities, evidence
from infracheck.core.geo import haversine_m
from infracheck.core.names import is_generic_directory, name_similarity, similar

HOME_CITY = "Архангельск"
DEFAULT_REGION = "20/arkhangelsk"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# A result list mixes organisations with plain addresses -- "улица Зеньковича,
# 8" arrives with a coordinate and no address of its own.  Those are not
# businesses and must not stand in for one, least of all as the "something is
# open at this door" that turns `absent` into `ambiguous`.  Written out rather
# than taken from `licmatch.TYPE_TABLE` wholesale: that table also holds
# "городок", and one of the objects in the queue is called exactly that.
TOPONYM_WORD = re.compile(
    r"\b(улиц\w*|проспект\w*|переул\w*|набережн\w*|шоссе|площад\w*|проезд\w*"
    r"|бульвар\w*|тракт|алле\w*|тупик|микрорайон\w*)\b", re.I)

CLOSED_MARK = "Больше не работает"
PAUSED_MARK = "Временно не работает"

STATUS_PRESENT = "present"      # organisation found at the address, open
STATUS_CLOSED = "closed"        # found, but Yandex marks it closed
STATUS_AMBIGUOUS = "ambiguous"  # the door is occupied, the export's name is generic
STATUS_ABSENT = "absent"        # nothing of that name at that door
STATUS_ERROR = "error"          # Yandex did not answer; nothing is claimed

# Yandex answers an automated burst with a one-word body; it is not an HTTP
# error, so it has to be recognised by sight and waited out.
LIMITED = re.compile(r"^\s*(?:<[^>]*>)*\s*limited\s*(?:<[^>]*>)*\s*$", re.I)

# Where a rubric list in a card's page title ends and the address begins.
RUBRIC_ADDR = re.compile(r"\d|\bул\.|улица|проспект|просп\.|\bпр\.|пр-т|\bш\.|\bпер\.|\bпл\.|наб\.|"
                         r"набережная|площадь|шоссе|переулок|бульвар|архангельск|северодвинск|новодвинск|округ|район")


@dataclass(frozen=True)
class Policy:
    """Everything that changes what the check says.

    `near_m` -- a hit must stand at the object's own door, not merely in its
    quarter: the export's coordinates come from 2GIS and OSM and agree with
    Yandex's to a few metres where both describe the same shop.  150 m is a
    building, not a street.  `generic_m` -- a generic fascia matches any other
    generic fascia, so those rows are accepted only at arm's length.
    `name_min` -- the name term decides alone once distance is spent, so it is
    set higher than the licence join's tie-breaker.  `contractor_miss_text` --
    what the weak contractor row says; it names the category's trade, so the
    category supplies it.
    """

    near_m: float = 150.0
    generic_m: float = 60.0
    name_min: float = 72.0
    contractor_status_found: str = "Найдено"
    contractor_miss_text: str = "по адресу есть общепит, но с другим названием"
    label: str = "Яндекс"
    label_contractor_miss: str = "Яндекс (подрядчик)"


DEFAULT = Policy()


# --------------------------------------------------------------------- network

def strip_city(address: str, city: str = HOME_CITY) -> str:
    """Drop the repeated city prefix: "Архангельск, улица Дрейера, 49" -> the rest."""
    return re.sub(rf"^\s*{re.escape(city)},\s*", "", str(address)).strip()


def search_url(name: str, address: str, lat: float, lon: float,
               city: str = HOME_CITY, region: str = DEFAULT_REGION) -> str:
    """The URL this check reads -- and the one to paste into a browser.

    Query and viewport both: the text is what a person would type, `ll` centres
    the map on the object so the answer is its neighbourhood's, not the city's.
    The city is repeated inside the query although the path already carries it,
    because neither the path nor `ll` binds a query Yandex cannot place: "Елена,
    улица Адмирала Макарова, 38" alone answers with a garden plot near Kirov.

    `region` is Yandex's own URL space: ``20/arkhangelsk``, ``10849/severodvinsk``.
    Yandex has no region-free search path (`/maps/search/` answers 404); a town
    without a region of its own is reached through Arkhangelsk's, which `ll`
    and the town named in the query then place -- checked on Novodvinsk.
    """
    q = f"{name}, {city}, {strip_city(address, city)}".strip(" ,")
    return (f"https://yandex.ru/maps/{region}/search/" + urllib.parse.quote(q, safe="")
            + f"/?ll={lon:.6f}%2C{lat:.6f}&z=17")


@dataclass
class Candidate:
    """One organisation Yandex put on the page, as the page states it."""

    oid: str = ""
    name: str = ""
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    closed: bool = False
    paused: bool = False

    @property
    def url(self) -> str:
        return f"https://yandex.ru/maps/org/{self.oid}/" if self.oid else ""


@dataclass
class Answer:
    """What one page said, before any judgement is passed on it.

    This is what goes into the cache, so that moving a threshold re-decides the
    list instead of re-reading it.
    """

    url: str = ""
    canonical: str = ""
    og_title: str = ""
    candidates: list[Candidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"search_url": self.url, "canonical": self.canonical,
                "og_title": self.og_title,
                "candidates": [vars(c) for c in self.candidates]}

    @classmethod
    def from_dict(cls, rec: dict) -> Answer:
        return cls(url=rec.get("search_url", ""),
                   canonical=rec.get("canonical", ""),
                   og_title=rec.get("og_title", ""),
                   candidates=[Candidate(**c) for c in rec.get("candidates", [])])


def _text(chunk: str) -> str:
    return _html.unescape(re.sub(r"<[^>]*>", " ", chunk)).strip()


def _first(pattern: str, chunk: str, group: int = 1) -> str:
    m = re.search(pattern, chunk, re.S)
    return _html.unescape(m.group(group)) if m else ""


def _coord(chunk: str, group: int) -> float | None:
    m = re.search(r'data-coordinates="([-0-9.]+),([-0-9.]+)"', chunk[:400])
    return float(m.group(group)) if m else None


def _card(chunk: str) -> Candidate:
    """A single resolved organisation: the page's own business card.

    The title is read to its closing tag rather than to the first `</`: the
    heading opens with an empty hidden span, and stopping at that span's end
    yields a name of "".
    """
    return Candidate(
        oid=_first(r'data-id="([^"]*)"', chunk[:400]),
        name=_text(_first(r'itemProp="name"[^>]*>(.*?)</h1>', chunk)),
        address=_first(r'itemProp="address" content="([^"]*)"', chunk),
        lat=_coord(chunk, 2), lon=_coord(chunk, 1),
        closed=CLOSED_MARK in chunk, paused=PAUSED_MARK in chunk,
    )


def _snippet(chunk: str) -> Candidate:
    """One row of a result list, whose markup differs from the single card."""
    return Candidate(
        oid=_first(r'data-id="([^"]*)"', chunk[:400]),
        name=_text(_first(r'search-business-snippet-view__title[^>]*>(.*?)</', chunk)),
        address=_text(_first(r'search-business-snippet-view__address[^>]*>(.*?)</div', chunk)),
        lat=_coord(chunk, 2), lon=_coord(chunk, 1),
        closed=CLOSED_MARK in chunk, paused=PAUSED_MARK in chunk,
    )


def parse(page: str, url: str = "") -> Answer:
    """Read the organisations off a rendered search page.

    Yandex serves two shapes for the same question -- one card when the query
    resolves to a single place, a list of snippets when it does not -- and the
    difference is markup only, so both are collected into one list.  The
    canonical link says which shape it was: `/maps/org/...` for a card,
    `/house/...` when only the address resolved, the search URL for a list.
    """
    a = Answer(url=url,
               canonical=_first(r'<link rel="canonical" href="([^"]*)"', page),
               og_title=_first(r'<meta property="og:title" content="([^"]*)"', page))
    for chunk in _blocks(page, 'data-object="search-result"'):
        a.candidates.append(_card(chunk))
    for chunk in _blocks(page, 'data-object="search-list-item"'):
        a.candidates.append(_snippet(chunk))
    return a


# Result-list items sit about 5,000 characters apart, so a block cut to a fixed
# length swallows the three that follow it -- and with them their "Больше не
# работает", which would then be read as this one's.  Every block therefore
# ends where the next result begins.
BLOCK_MAX = 20000


def _blocks(page: str, marker: str):
    """The page's result blocks, each ending where the next result starts."""
    for part in page.split(marker)[1:]:
        cut = part.find('data-object="')
        yield part[:cut if cut != -1 else BLOCK_MAX]


def fetch(url: str, *, tries: int = 5, pause: float = 5.0) -> str:
    """One page, retried patiently: a refusal here is a rate limit, not a 404.

    The backoff doubles from five seconds rather than starting at twenty: a
    throttled run spends most of its wall clock in this function, and Yandex
    lets the next request through far sooner than a flat long pause assumes.
    """
    last = ""
    for i in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "text/html,application/xhtml+xml"})
        try:
            page = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
            if not LIMITED.match(page) and len(page) > 5000:
                return page
            last = "rate-limited" if LIMITED.match(page) else f"short page ({len(page)} b)"
        # `IncompleteRead` is an `HTTPException`, not an `OSError`: a throttled
        # connection is as likely to be cut mid-body as refused outright, and
        # leaving it uncaught ends a two-hour run at row 118.
        except (urllib.error.URLError, http.client.HTTPException,
                TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(pause * 2 ** i)
    raise RuntimeError(last or "no answer")


def read(name: str, address: str, lat: float, lon: float, *,
         sleep: tuple[float, float] = (2.5, 4.5), city: str = HOME_CITY,
         region: str = DEFAULT_REGION) -> dict:
    """Read one object's page and return what it said -- no verdict, no opinion.

    This is the only function that touches the network, and its result is what
    the cache stores.
    """
    url = search_url(name, address, lat, lon, city, region)
    try:
        page = fetch(url)
    except RuntimeError as e:
        return {"search_url": url, "canonical": "", "og_title": "",
                "candidates": [], "note": str(e),
                "checked": date.today().isoformat()}
    answer = parse(page, url)
    time.sleep(random.uniform(*sleep))
    return {**answer.to_dict(), "note": "",
            "checked": date.today().isoformat()}


def load_cache(path) -> dict[str, dict]:
    """Everything checked so far, keyed by the query (`search_url`); later lines win.

    The query, not the object, is what an answer answers.  A v4 `site_key` is
    the coordinate alone, so an alcohol and a tobacco record at one door share
    it while each is searched by its own name; keyed by site, the second answer
    would overwrite the first.  Each line still carries its `site_key`.
    """
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["search_url"]] = rec
    return out


def append_cache(rec: dict, path) -> None:
    """One line per object, appended as it is read -- a long run is resumable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------- judge

def is_toponym(cand: Candidate) -> bool:
    """True where the result is an address, not a business.

    Yandex answers an address query with the address itself alongside whatever
    stands at it.  Such a result carries a coordinate but no address of its own
    and is named for the street; a business named "Штаб" may also arrive
    without an address, which is why both conditions are required.
    """
    return not cand.address and bool(TOPONYM_WORD.search(cand.name))


def _score(cand: Candidate, name: str, lat: float, lon: float) -> tuple[float, float]:
    """(metres from the object, name similarity) for one candidate."""
    if cand.lat is None or cand.lon is None:
        return float("inf"), 0.0
    return (float(haversine_m(lat, lon, cand.lat, cand.lon)),
            name_similarity(name, cand.name))


def judge(answer: Answer, name: str, lat: float, lon: float, *,
          generic: bool | None = None, policy: Policy = DEFAULT) -> dict:
    """Decide what the page says about *this* object.

    A candidate is taken only when it is both near enough and named alike; a
    generic fascia has to be nearer still, because "Продукты" matches every
    other "Продукты" in town and distance is then the only real evidence.

    Where the export's name is a trade descriptor and something *is* open at
    that door, the answer is neither yes nor no and is not forced into one:
    `ambiguous` says the address is occupied and the name cannot decide whose
    it is.  The nearest rejected candidate is kept in every negative case --
    for a row that comes out `absent`, what *does* stand there is the next
    thing a reader asks.

    `generic` overrides the directory rule for a caller whose trade has other
    nameless fascias -- "Столовая" is generic in catering, "Продукты" in retail.
    """
    orgs = [c for c in answer.candidates if not is_toponym(c)]
    scored = [(*_score(c, name, lat, lon), c) for c in orgs]
    scored.sort(key=lambda t: (t[0], -t[1]))
    if generic is None:
        generic = is_generic_directory(name)
    limit = policy.generic_m if generic else policy.near_m

    best = next((t for t in scored if t[0] <= limit and t[1] >= policy.name_min), None)
    if best is None:
        near = scored[0] if scored and scored[0][0] <= policy.near_m else None
        occupied = near is not None and near[0] <= policy.generic_m
        return {
            "status": (STATUS_AMBIGUOUS if generic and occupied else STATUS_ABSENT),
            "yandex_name": "", "yandex_address": "", "yandex_url": "",
            "yandex_distance_m": "", "yandex_name_sim": "",
            "yandex_nearby": (f"{near[2].name} — {near[0]:.0f} м, {near[2].address}"
                              if near else ""),
            "address_found": occupied or "/house/" in answer.canonical,
        }
    d, sim, c = best
    return {
        "status": STATUS_CLOSED if (c.closed or c.paused) else STATUS_PRESENT,
        "yandex_name": c.name, "yandex_address": c.address, "yandex_url": c.url,
        "yandex_distance_m": round(d, 1), "yandex_name_sim": round(sim, 1),
        "yandex_nearby": "", "address_found": True,
    }


def verdict(rec: dict, name: str, lat: float, lon: float, *,
            generic: bool | None = None, policy: Policy = DEFAULT) -> dict:
    """Judge one cached answer.  Costs nothing, so thresholds stay movable."""
    if rec.get("note"):
        return {"status": STATUS_ERROR, "yandex_name": "", "yandex_address": "",
                "yandex_url": "", "yandex_distance_m": "", "yandex_name_sim": "",
                "yandex_nearby": "", "address_found": False}
    return judge(Answer.from_dict(rec), name, lat, lon, generic=generic, policy=policy)


def rubric(rec: dict | None, url: str) -> str:
    """Yandex's rubrics for the card our check matched, from the page title."""
    if not rec or not url:
        return ""
    oid = re.search(r"/(\d{6,})/?", url)
    if not oid or oid.group(1) not in (rec.get("canonical") or ""):
        return ""
    title = re.sub(r"^Больше не работает:\s*", "", rec.get("og_title") or "")
    title = title.split(" — Яндекс")[0]
    parts = [p.strip() for p in title.split(",")][1:]   # drop the name
    out = []
    for p in parts:
        if RUBRIC_ADDR.search(p.lower()):
            break
        out.append(p)
    return ", ".join(out)


def rejudge_contractor(rows_json: str, name: str, policy: Policy = DEFAULT) -> dict:
    """The contractor's «Найдено», kept only where Yandex's name is ours too.

    `rows_json` is the adapter's `yandex_contractor_rows`: the found rows of
    one object as `[names, address, url]`.  `names` holds every name the card
    list showed at the address, "; "-separated; the best of them is compared.
    A miss here is not "absent" -- it only means the contractor's hit does not
    name this object.  Returns `raw` (any «Найдено»), `ok`, and the first
    non-empty `name`/`addr`/`url` over the rows that matched.
    """
    out = {"raw": False, "ok": False, "name": "", "addr": "", "url": "", "sim": float("nan")}
    names, addrs, urls, sims = [], [], [], []
    for names_s, addr, url in json.loads(rows_json or "[]"):
        out["raw"] = True
        cands = [n.strip() for n in str(names_s).split(";") if n.strip() and n.strip() != "nan"]
        scored = [(similar(name, n), n) for n in cands]
        sim, best = max(scored, default=(0.0, ""))
        sims.append(sim)
        if sim >= policy.name_min:
            names.append(best)
            addrs.append(addr)
            urls.append(url)
    if names:
        out.update(ok=True, name=entities.first_nonempty(pd.Series(names)),
                   addr=entities.first_nonempty(pd.Series(addrs)),
                   url=entities.first_nonempty(pd.Series(urls)))
    if sims:
        out["sim"] = float(max(sims))
    return out


# -------------------------------------------------------------------- evidence

def _own_clause(v: dict, checked: str, search: str) -> tuple[str, str]:
    """(identity/state key, text) of our own verdict: the body of the clause."""
    status = v["status"]
    if status == STATUS_PRESENT:
        return "present", (f"карточка «{v['yandex_name']}», {v['yandex_address']}, {v['yandex_distance_m']} м"
                           f" (наша проверка {checked}; {v['yandex_url'] or search})")
    if status == STATUS_CLOSED:
        return "closed", f"«{v['yandex_name']}» — «Больше не работает»"
    if status == STATUS_ABSENT:
        return "absent", ("по названию и адресу не найден"
                          + (f" (рядом: {v['yandex_nearby']})" if v["yandex_nearby"] else ""))
    if status == STATUS_AMBIGUOUS:
        return "ambiguous", f"по адресу кто-то есть, вывеска безымянная (рядом: {v['yandex_nearby']})"
    return "error", ""


_AXES = {   # status -> (identity, record_state)
    STATUS_PRESENT: ("strong", "current"), STATUS_CLOSED: ("strong", "closed"),
    STATUS_AMBIGUOUS: ("weak", "current"), STATUS_ABSENT: ("none", "unknown"),
    STATUS_ERROR: ("none", "unknown"),
}


def own_query(r: pd.Series) -> str:
    """The search URL an object's own name and address make (the cache key)."""
    return search_url(r["name"], r["address"], float(r["lat"]), float(r["lon"]), r["city"],
                      r.get("yandex_region") or DEFAULT_REGION)


def run(objects: pd.DataFrame, policy: Policy = DEFAULT,
        cache: dict[str, dict] | None = None) -> pd.DataFrame:
    """Evidence of Yandex Maps for every object.

    ``yandex_contractor`` rows (``source = "yandex"``): the contractor's hit
    re-judged by name -- strong/current «Яндекс» where the name matched, weak/
    current «Яндекс (подрядчик)» for a raw «Найдено» that names someone else.
    ``yandex`` rows: our own query, judged from `cache` (keyed by query, see
    `load_cache`; an answer counts only for the object whose name and address
    make that query; objects never read have no row) -- present strong/current, closed strong/closed, ambiguous weak,
    absent none, error none/unknown with empty text.  `kind_text` is the card's
    rubric (``yandex_rubric``).  Extra columns: `yandex_status`,
    `yandex_nearby`, `yandex_search_url`, `yandex_checked`.
    """
    cache = cache or {}
    rows = []
    for oid, r in objects.iterrows():
        c = rejudge_contractor(r.get("yandex_contractor_rows", "[]"), str(r["name"]), policy)
        if c["ok"]:
            rows.append({
                "object_id": oid, "check": "yandex_contractor", "source": "yandex",
                "identity": "strong", "record_state": "current",
                "matched_name": c["name"], "matched_address": c["addr"], "name_score": c["sim"],
                "ref": c["url"], "label": policy.label,
                "text": f"карточка «{c['name']}», {c['addr']} (проверка подрядчика"
                        + (f"; {c['url']}" if c["url"] else "") + ")"})
        elif c["raw"]:
            rows.append({
                "object_id": oid, "check": "yandex_contractor", "source": "yandex",
                "identity": "weak", "record_state": "current", "name_score": c["sim"],
                "label": policy.label_contractor_miss, "text": policy.contractor_miss_text})
        rec = cache.get(own_query(r))
        if rec is None:
            continue
        v = verdict(rec, str(r["name"]), float(r["lat"]), float(r["lon"]),
                    generic=bool(r["generic"]), policy=policy)
        search = rec.get("search_url", "")
        _, text = _own_clause(v, rec.get("checked", ""), search)
        ident, state = _AXES[v["status"]]
        rows.append({
            "object_id": oid, "check": "yandex", "source": "yandex",
            "identity": ident, "record_state": state,
            "kind_text": rubric(rec, v["yandex_url"]), "kind_scheme": "yandex_rubric",
            "matched_name": v["yandex_name"], "matched_address": v["yandex_address"],
            "distance_m": v["yandex_distance_m"] if v["yandex_distance_m"] != "" else np.nan,
            "name_score": v["yandex_name_sim"] if v["yandex_name_sim"] != "" else np.nan,
            "ref": v["yandex_url"], "label": policy.label, "text": text,
            "yandex_status": v["status"], "yandex_nearby": v["yandex_nearby"],
            "yandex_search_url": search, "yandex_checked": rec.get("checked", "")})
    return evidence.make(rows) if rows else evidence.empty()


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """The archive's columns, indexed by object_id: `ya_contractor`,
    `ya_contractor_raw`, `ya_c_name`, `ya_c_addr`, `ya_c_url`, `ya_status`,
    `ya_name`, `ya_address`, `ya_url`, `ya_distance_m`, `ya_nearby`,
    `ya_search_url`, `ya_checked`.  Objects never read have "" in the `ya_*`
    columns (as in the archive); pass `index` to keep all objects."""
    ids = index if index is not None else pd.Index(ev["object_id"].unique(), name="object_id")
    out = pd.DataFrame(index=ids)
    out.index.name = "object_id"
    con = ev[ev["check"] == "yandex_contractor"].set_index("object_id")
    ok = con[con["identity"] == "strong"]
    out["ya_contractor"] = out.index.isin(ok.index)
    out["ya_contractor_raw"] = out.index.isin(con.index)
    for col, src in (("ya_c_name", "matched_name"), ("ya_c_addr", "matched_address"), ("ya_c_url", "ref")):
        out[col] = ok[src].astype(object).reindex(out.index).fillna("")
    own = ev[ev["check"] == "yandex"].set_index("object_id")
    for col, src in (("ya_status", "yandex_status"), ("ya_name", "matched_name"),
                     ("ya_address", "matched_address"), ("ya_url", "ref"),
                     ("ya_nearby", "yandex_nearby"), ("ya_search_url", "yandex_search_url"),
                     ("ya_checked", "yandex_checked")):
        out[col] = own[src].astype(object).reindex(out.index).fillna("")
    dist = own["distance_m"].astype(object).reindex(out.index)
    out["ya_distance_m"] = [float(x) if pd.notna(x) else "" for x in dist]
    return out[["ya_contractor", "ya_contractor_raw", "ya_c_name", "ya_c_addr", "ya_c_url",
                "ya_status", "ya_name", "ya_address", "ya_url", "ya_distance_m",
                "ya_nearby", "ya_search_url", "ya_checked"]]


# ------------------------------------------------------------------------- CLI

def fetch_missing(ob: pd.DataFrame, cache: dict, cache_path, *, limit: int = 0,
                  sleep: tuple[float, float] = (2.5, 4.5)) -> int:
    """Read the pages missing from the cache, appending as it goes.

    A failure is not an answer: not cached, asked again next run.  Returns the
    number of pages cached.
    """
    urls = [own_query(r) for _, r in ob.iterrows()]
    todo = [r for (_, r), url in zip(ob.iterrows(), urls, strict=True) if url not in cache]
    if limit:
        todo = todo[:limit]
    print(f"  Yandex: {len(ob):,} objects in scope, "
          f"{sum(u in cache for u in urls):,} cached, {len(todo):,} to read now")
    done = 0
    for i, r in enumerate(todo, 1):
        try:
            rec = read(r["name"], r["address"], float(r["lat"]), float(r["lon"]),
                       sleep=tuple(sleep), city=r["city"],
                       region=r.get("yandex_region") or DEFAULT_REGION)
        except Exception as e:  # noqa: BLE001 -- one row must not end the run
            rec = {"candidates": [], "note": f"{type(e).__name__}: {e}"}
        rec["site_key"] = r["site_key"]
        if rec["note"]:
            print(f"    [{i:>4}/{len(todo)}] -- failed  {rec['note']}", flush=True)
            continue
        append_cache(rec, cache_path)
        cache[rec["search_url"]] = rec
        done += 1
        print(f"    [{i:>4}/{len(todo)}] {len(rec['candidates']):>2} found", flush=True)
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m infracheck.checks.yandex", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="read Yandex Maps pages missing from the cache (network)")
    f.add_argument("--objects", required=True, help="ObjectTable parquet")
    f.add_argument("--cache", required=True, help="JSONL cache, appended to")
    f.add_argument("--only-ids", help="file with one object_id per line; default: every object")
    f.add_argument("--limit", type=int, default=0, help="read at most N pages")
    f.add_argument("--sleep", type=float, nargs=2, default=(2.5, 4.5), metavar=("MIN", "MAX"),
                   help="courtesy pause between requests")
    f.add_argument("--refresh", action="store_true", help="ignore the cache: re-read everything in scope")
    args = p.parse_args(argv)

    ob = pd.read_parquet(args.objects)
    if "object_id" in ob.columns:
        ob = ob.set_index("object_id")
    if args.only_ids:
        ids = [x.strip() for x in Path(args.only_ids).read_text(encoding="utf-8").splitlines() if x.strip()]
        ob = ob.loc[ob.index.intersection(ids)]
    cache = {} if args.refresh else load_cache(args.cache)
    fetch_missing(ob, cache, args.cache, limit=args.limit, sleep=tuple(args.sleep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
