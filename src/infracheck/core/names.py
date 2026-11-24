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


# Number signs: «No 4», «№ 4», «N 4» are one hospital.
_NUMBER_SIGNS = frozenset({"no", "n", "nr"})


def _words(text: str) -> set[str]:
    return {t for t in re.sub(r"[^0-9a-zа-я ]+", " ", fold(text)).split() if t not in _NUMBER_SIGNS}


def contains_words(export: str, directory: str, min_chars: int = 5) -> bool:
    """True where every word of the export's fascia stands in the directory's name.

    The directory often writes a medical organisation in full where the
    export keeps the short form: «Окродент» / «Стоматология Окродент»,
    «Центр ядерной медицины» / «СМКЦ имени Н. А. Семашко, центр ядерной
    медицины», «Областная больница» / «Архангельская областная клиническая
    больница, хирургический корпус».  `name_similarity` rejects these on
    purpose (a chain's name inside a longer one is another shop: «Магнит» /
    «Магнит Косметик»), so this rule is opt-in per category and is used only
    at a short distance (the Yandex `contain_m` policy).  The part before the
    first comma is the fascia; a trade-only name (`is_generic_directory`) or a
    key shorter than `min_chars` never qualifies.
    """
    if is_generic_directory(export):
        return False
    key = fascia_name(str(export).split(",")[0]) or fold(str(export).split(",")[0])
    words = _words(key)
    if not words or len("".join(words)) < min_chars:
        return False
    return words <= _words(directory)


# «Детский сад No101», «школа № 34», «N 4»: the number an institution goes by.
_INST_NUMBER = re.compile(r"(?:№|\bno\.?|\bn)\s*(\d{1,4})\b")
# The kind of institution a name opens with.  ORDER IS THE RULE where two
# patterns start at one place: «школа искусств» is an art school before it is
# a school.  Schools, kindergartens, art and sports schools are numbered apart.
_INST_KINDS = [
    ("art_school", r"школ\w* искусств|\bдши\b|музыкальн\w* школ|художественн\w* школ|хореографическ\w* школ"),
    ("sport_school", r"спортивн\w* школ|\bдюсш|\bсдюсшор|\bсшор|спортшкол"),
    ("creativity", r"творчеств|юных техник|юных натурал"),
    ("kindergarten", r"детск\w* сад|\bдетсад|дошкольн|\bясли"),
    ("college", r"колледж|техникум|училищ"),
    ("school", r"школ|гимнази|лице[йя]"),
    ("women", r"женск\w* консультац"),
    ("polyclinic", r"поликлиник"),
    ("hospital", r"больниц|\bгкб\b|\bгб\b|\bцгб\b|\bнцгб\b|\bцрб\b|госпитал"),
    ("ambulance", r"подстанц|скор\w* медицинск"),
    ("dental", r"стоматолог"),
    ("pharmacy", r"аптек|аптечн"),
    # «Отделение врача общей практики» / «…, центр общей врачебной практики»
    ("general_practice", r"общ\w* (?:врачебн\w* )?практик"),
]
_INST_KIND_RX = [(k, re.compile(rx)) for k, rx in _INST_KINDS]
# Trades that carry no number but do have a plain name: «Оптика», «Бассейн».
_TRADE_KIND_RX = _INST_KIND_RX + [(k, re.compile(rx)) for k, rx in [
    ("optics", r"оптик|очк"), ("lab", r"лаборатор"), ("clinic", r"клиник|медицинск\w* центр|медцентр"),
    ("pool", r"бассейн"), ("stadium", r"стадион"), ("gym", r"спортзал|спортивн\w* зал|фитнес|тренажерн")]]
# Words that qualify a trade without naming a business: «Взрослая поликлиника»,
# «Магазин готовых очков», «Детский сад комбинированного вида».
_TRADE_QUALIFIERS = frozenset({
    "взрослая", "взрослый", "взрослых", "детская", "детский", "детских", "детское", "городская", "городской",
    "областная", "областной", "центральная", "центральный", "районная", "районный", "клиническая", "клинический",
    "первая", "вторая", "третья", "отделение", "отделения", "офис", "кабинет", "врача", "общей", "практики",
    "врачебной", "медицинской", "медицинский", "медицинская", "помощи", "филиал", "пункт", "пункта", "участок",
    "средняя", "основная", "начальная", "общеобразовательная", "комбинированного", "общеразвивающего", "вида",
    "учреждения", "учреждение", "дошкольные", "дошкольное", "магазин", "салон", "центр", "дисконт", "готовых",
    "при", "имени", "муниципальное", "бюджетное", "государственное", "автономное", "гбуз", "гбоу", "мбоу",
    "мбдоу", "мадоу", "для"})


def _institution(name: str) -> tuple[str, int | None]:
    """(kind of the first institution word, first institution number) of one name."""
    s = fold(str(name))
    hits = [(m.start(), i, k) for i, (k, rx) in enumerate(_INST_KIND_RX) if (m := rx.search(s))]
    kind = min(hits)[2] if hits else ""
    num = _INST_NUMBER.search(s)
    return kind, (int(num.group(1)) if num else None)


# Public medical institutions are named by place, not by brand: «Исакогорская
# подстанция скорой медицинской помощи», «Соломбальская поликлиника».  There an
# adjective is a district; «Добрая аптека» is a chain, so trades stay out.
_PUBLIC_KINDS = frozenset({"ambulance", "hospital", "polyclinic", "women", "general_practice"})
_ADJECTIVE = re.compile(r"(?:ая|яя|ий|ый|ой|ое|ее|ые|ие|ых|их|ого|его|ей|ую|юю)$")
_QUOTES = re.compile(r"[«»\"“”„]")


def trade_kind(name: str, public_places: bool = False) -> str:
    """The trade a name gives where it gives nothing else, «» for a business name.

    «Взрослая поликлиника» -> «polyclinic», «Магазин готовых очков» -> «optics»,
    «Городская больница No 4» -> «hospital»; «Фармомед», «Детский сад
    "Незабудка"», «Аптека Антей» -> «» (a name of its own is compared by name).
    With `public_places`, a public medical institution qualified only by
    adjectives and without a quoted name keeps its kind: «Исакогорская
    подстанция скорой медицинской помощи» -> «ambulance»; «Поликлиника
    "Здоровье"», «Добрая аптека» -> «».
    """
    s = _INST_NUMBER.sub(" ", fold(str(name)).replace("ё", "е"))
    hits = sorted((m.start(), i, k) for i, (k, rx) in enumerate(_TRADE_KIND_RX) for m in rx.finditer(s))
    if not hits:
        return ""
    rest = s
    for _, rx in _TRADE_KIND_RX:  # cut each kind word whole, ending included («поликлиник|а»)
        rest = re.sub(rf"\w*(?:{rx.pattern})\w*", " ", rest)
    leftovers = [w for w in re.findall(r"[a-zа-я]+", rest) if len(w) >= 3 and w not in _TRADE_QUALIFIERS]
    if (leftovers and public_places and hits[0][2] in _PUBLIC_KINDS and not _QUOTES.search(str(name))
            and all(_ADJECTIVE.search(w) for w in leftovers)):
        return hits[0][2]
    return "" if leftovers else hits[0][2]


def has_kind(kind: str, name: str) -> bool:
    """Does `name` mention the trade `kind` anywhere («…, поликлиника» has «polyclinic»)?"""
    rx = dict(_TRADE_KIND_RX).get(kind)
    return bool(rx and rx.search(fold(str(name)).replace("ё", "е")))


def number_agreement(export: str, directory: str) -> bool | None:
    """Do two names give the same institution its number?  None where that cannot be told.

    The export writes «No101» or «No 34», Yandex and Overture «№ 101»; the
    ratio rule scores «Детский сад No101 комбинированного вида» / «Детский сад
    № 101» below its threshold, yet «Средняя школа No 34» / «Средняя школа
    № 43» at 86.  Only where both names open with the same kind of institution
    (`_INST_KINDS`) and both carry a number: the same first number is one
    institution (True), a different one never is (False).  Otherwise -- «Средняя
    школа № 60» / «Детский сад при школе № 60», «Детская школа искусств № 2» /
    «Средняя школа № 2», no number on one side -- the similarity rule decides
    (None).  Opt-in per check through the `numbers` policy field.
    """
    ka, na = _institution(export)
    kb, nb = _institution(directory)
    if na is None or nb is None or not ka or ka != kb:
        return None
    return na == nb


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
