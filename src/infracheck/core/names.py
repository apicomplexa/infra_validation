"""Comparing two fascias: the name rules every directory check shares.

Ported verbatim from the archive repository (`arkhinfra.v4_yandex` and
`arkhinfra.final_confirm`, tag `archive-2026-10-30`).  Two similarity functions
live here side by side on purpose: `name_similarity` (directory-asymmetric, the
Yandex rule) and `similar` (the same plus a transliterated retry, used by the
final delivery for relinking, Overture and Foursquare).  Unifying them changes
published numbers, so it is a separate, reviewed step -- not part of the port.
"""
from __future__ import annotations

import re

from licmatch import fascia_name, fold
from rapidfuzz import fuzz

# The name term decides alone once distance is spent, so it is set higher than
# the licence join's tie-breaker: "Продукты" must not claim the "Пятёрочка"
# next door.
NAME_MIN = 72.0
# How much of the *directory's* name the export's has to be before a subset
# match is allowed.  "beer honey" is 56 % of "beer honey brewery"; "бакарица"
# is 33 % of "мук ицкц филиал бакарица".
CONTAIN_MIN = 0.5
CONTAIN_MIN_CHARS = 4
# A generic fascia matches any other generic fascia, so those rows are accepted
# only at arm's length -- one building's depth, not the block's.
GENERIC = frozenset({"продукты", "продукты 24", "продуктовый", "минимаркет",
                     "вино", "разливное", "разливные напитки", "пиво",
                     "живое пиво", "пивной", "табак", "алкоголь", "универсам",
                     "food", "еда", "24 часа", "круглосуточно"})

# Words that name a kind of catering rather than a business.  An object whose
# fascia is made of nothing else ("Кофейный автомат", "Шаурма", "Пекарня") can
# be matched by distance only, never by name.  `fascia_name` already drops
# кафе / бар / ресторан / столовая / кулинария / пекарня / кофейня.
GENERIC_TOKENS = frozenset({
    "кофе", "кофейный", "кофейная", "автомат", "автоматы", "вендинг",
    "самообслуживания", "собой", "навынос", "буфет", "бистро", "закусочная",
    "фастфуд", "фаст", "фуд", "food", "шаурма", "шаверма", "пицца", "пиццерия",
    "суши", "роллы", "пончики", "пончиковая", "блины", "блинная", "пироги",
    "пирожковая", "выпечка", "хлеб", "хлебный", "кондитерская", "чебуречная",
    "пельменная", "шашлык", "шашлычная", "гриль", "бургерная", "кафе-бар",
    "ресто-бар", "кофе-бар", "столовая", "еда", "обеды", "горячие", "напитки",
    "чайная", "кухня", "домашняя", "уличная", "точка", "киоск", "павильон",
    "магазин", "овощей", "фруктов", "и", "с", "на", "24", "часа",
    "кофе-пойнт", "кофепойнт", "коктейльная", "пивной", "бар-ресторан",
    "шаурменная", "шаверменная", "булочная", "кулинарная", "хлебная", "лавка",
    "кофейня", "пекарня", "кулинария", "мини", "экспресс",
})

# The export and the directories write one sign in either alphabet -- «Stare
# Mesto» / «Старе Место», «Polina» / «Полина».  Latin is read into Cyrillic the
# way a Russian reader would say it; digraphs first.
_LAT_DIGRAPHS = [("shch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"),
                 ("ts", "ц"), ("ya", "я"), ("yu", "ю"), ("yo", "ё"), ("ee", "и"),
                 ("oo", "у"), ("ph", "ф"), ("ck", "к"), ("th", "т")]
_LAT_SINGLE = str.maketrans(dict(zip(
    "abcdefghijklmnopqrstuvwxyz",
    ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м", "н", "о",
     "п", "к", "р", "с", "т", "у", "в", "в", "кс", "й", "з"], strict=True)))


def _keys(name: str) -> list[str]:
    """The forms of one export name worth comparing, best first.

    The delivery writes a fascia and then its trade: "Штаб, ресто-кафе".
    What survives the comma is compared as well as the whole, and whichever
    form scores better wins.
    """
    forms = [fascia_name(name.split(",")[0]), fascia_name(name)]
    return [f for f in dict.fromkeys(forms) if f]


def name_similarity(export: str, directory: str) -> float:
    """How alike two fascias are, on the distinctive part where there is one.

    The two sides are *not* symmetric.  When the directory's whole name sits
    inside the export's, that is the same business described at two lengths.
    A name the *directory* extends is accepted only where the export's name is
    most of it (`CONTAIN_MIN`).  Short of containment, `ratio` and
    `token_sort_ratio` are both tried.  Where `fascia_name` empties a side the
    written names are compared instead.
    """
    ka_list, kb = _keys(export), fascia_name(directory)
    if not ka_list or not kb:
        ka_list, kb = [fold(export)], fold(directory)
    if not kb or not any(ka_list):
        return 0.0
    return max(_pair_similarity(ka, kb) for ka in ka_list if ka)


def _pair_similarity(ka: str, kb: str) -> float:
    """`ka` is the export's key, `kb` the directory's.  Order matters."""
    sim = max(float(fuzz.ratio(ka, kb)), float(fuzz.token_sort_ratio(ka, kb)))
    ta, tb = set(ka.split()), set(kb.split())
    if tb < ta and len(kb) >= CONTAIN_MIN_CHARS:
        return max(sim, 100.0)
    if ta <= tb and len(kb) and len(ka) / len(kb) >= CONTAIN_MIN:
        return max(sim, float(fuzz.token_set_ratio(ka, kb)))
    return sim


def is_generic_directory(name: str) -> bool:
    """True where the fascia names a trade rather than a business (Yandex rule)."""
    key = fascia_name(name)
    return key == "" or key in GENERIC


def is_generic(name: str) -> bool:
    """True where the fascia names a trade, not a business (final-delivery rule).

    Wider than `is_generic_directory`: a name made only of `GENERIC_TOKENS`
    ("Кофейный автомат") is generic too.
    """
    if is_generic_directory(name):
        return True
    toks = re.sub(r"[^0-9a-zа-я\- ]+", " ", fold(name)).split()
    return all(t in GENERIC_TOKENS for t in toks)


def translit(name: str) -> str:
    """Latin letters read into Cyrillic; Cyrillic and digits left alone."""
    s = fold(name)
    for a, b in _LAT_DIGRAPHS:
        s = s.replace(a, b)
    return s.translate(_LAT_SINGLE)


def similar(a: str, b: str) -> float:
    """`name_similarity`, also tried with both sides in Cyrillic."""
    sim = name_similarity(a, b)
    if sim >= NAME_MIN or not re.search(r"[a-z]", fold(a + b)):
        return sim
    return max(sim, name_similarity(translit(a), translit(b)))
