"""Do two names say the same venue or company?  A yes/no rule on whole words.

`scoring`'s name term is a tie-breaker: a WRatio between two keys, weighted
0.20, never the evidence that a pair is right.  A register whose addresses are
shared -- a shopping centre or a hotel holds a food court's worth of venues and
a handful of licences, and the register's coordinate cannot tell them apart --
needs the opposite: a pair is accepted only when the names *agree*, and the
agreement has to be a decision a reader can check, not a number.

WRatio is the wrong tool for that decision: it falls back on a partial ratio
when one key is much shorter, which reads holder "миг" into a fascia "мигрант"
at 0.90.  `names_agree()` works on whole words instead, and on a Latin
transliteration as well as the keys themselves, because a fascia and its holder
are often one name in two scripts ("El Fuego" / ООО «Эль Фуэго»).

Ported from the archive's `final_egais` (catering, three cities), where it was
calibrated.  The vocabulary below is Russian catering; place words are the
caller's (`place_words=`), since they belong to a region, not to the rule.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from rapidfuzz import fuzz

from .names import brand_key, is_generic

NAME_RATIO_MIN = 85        # whole-string agreement, fuzz.ratio
NAME_SHARED_MIN_LEN = 5    # a shared word this long agrees on its own ("холмс")
# Keys this short carry too little to agree on by string ("бп", "кс").
NAME_MIN_LEN = 3
# Words that name a kind of venue or a product, not a venue.  A fascia made of
# nothing else is generic, and such a word never agrees on its own -- "Кофе" is
# not ООО «Кофе Хаус».
VENUE_WORDS = frozenset({
    "кофе", "coffee", "cafe", "пицца", "pizza", "пиццерия", "суши", "sushi",
    "роллы", "вок", "wok", "гриль", "grill", "шаурма", "шаверма", "шашлык",
    "шашлыки", "бургер", "бургерная", "хлеб", "пекарня", "кондитерская",
    "кафе-бар", "кафе-пекарня", "кафе-кондитерская", "ресторан-кафе",
    "кафе-ресторан", "кафе-столовая", "рестобар", "гастропаб", "гастробар",
    "лаундж", "лаундж-бар", "lounge", "bar", "pub", "грузинской", "японской",
    "кухни", "кухня", "семейное", "семейный", "быстрого", "питания",
    "предприятие", "служба", "доставки", "буфет", "столовая", "блюд",
    "фуд", "food", "групп", "group", "плюс", "сервис", "хаус", "house",
    "русской", "кофейня", "киоск", "автомат", "кофейный",
    "вкусно", "вкусная", "вкусный", "вкус", "точка", "экспресс", "бар-маркет",
})

_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya"})


def latin(key: str) -> str:
    """A folded key in Latin letters, so a name written in either script compares."""
    return key.translate(_LATIN)


def is_generic_name(key: str) -> bool:
    """No word of the key names this venue rather than a kind of venue."""
    toks = brand_key(key).replace("-", " ").split()
    return (len(key) < NAME_MIN_LEN or is_generic(key)
            or all(t in VENUE_WORDS or t.isdigit() or len(t) < 2 for t in toks))


@lru_cache(maxsize=8)
def _common(place_words: frozenset) -> frozenset:
    """Venue and place words, in both scripts: never a shared name on their own."""
    common = VENUE_WORDS | place_words
    return frozenset({latin(t) for t in common} | common)


def names_agree(x: str, y: str, place_words: frozenset = frozenset()) -> bool:
    """Do two name keys say the same venue / company?

    Yes when the whole strings agree (fuzz.ratio >= NAME_RATIO_MIN), when every
    word of one is a word of the other ("мясо" / "гастробар мясо", "осень" /
    "золотая осень"), or when they share a distinctive word of at least
    NAME_SHARED_MIN_LEN letters ("холмс" / "холмс фуд") -- each tried on the
    keys and on their Latin transliteration.  A generic key (VENUE_WORDS only)
    never agrees, and neither the subset nor the shared word may consist of
    common words alone: VENUE_WORDS, and the caller's `place_words` -- a venue
    may well be called "Двина" (and ООО «Двина» run it), so a place word does
    not make a name generic, but a shared place word alone is not a shared name.
    """
    if is_generic_name(x) or is_generic_name(y):
        return False
    common = _common(frozenset(place_words))
    for u, v in ((x, y), (latin(x), latin(y))):
        if fuzz.ratio(u, v) >= NAME_RATIO_MIN:
            return True
        tu = {t for t in brand_key(u).split() if len(t) > 1}
        tv = {t for t in brand_key(v).split() if len(t) > 1}
        small = tu if len(tu) <= len(tv) else tv
        if small and small <= (tu & tv) and not small <= common:
            return True
        if any(len(t) >= NAME_SHARED_MIN_LEN and t not in common for t in tu & tv):
            return True
    return False


def agree_matrix(a_keys, b_keys, place_words: frozenset = frozenset()) -> np.ndarray:
    """(a x b) mask of `names_agree()`, each distinct pair of keys compared once."""
    ua, ia = np.unique(np.asarray(a_keys, dtype=object), return_inverse=True)
    ub, ib = np.unique(np.asarray(b_keys, dtype=object), return_inverse=True)
    ok = np.array([[names_agree(x, y, place_words) for y in ub] for x in ua],
                  dtype=bool).reshape(len(ua), len(ub))
    return ok[ia][:, ib]


__all__ = ["NAME_RATIO_MIN", "NAME_SHARED_MIN_LEN", "NAME_MIN_LEN", "VENUE_WORDS",
           "latin", "is_generic_name", "names_agree", "agree_matrix"]
