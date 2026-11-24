"""Street and house of a Russian address, as a key two sources can share.

The export writes «Гайдара, 3», «Ленинградский проспект, 40к3»; a Yandex card
«Архангельск, ул. Гайдара, 3», «Ленинградский просп., 40, корп. 3, 163045 ·
этаж 2».  `address_key` reduces both to (street words, house): the city, the
district, the postcode, the floor and the street type are dropped, the
korpus and the building («с1», «стр. 1») join the house number.  An address
without a house number has no key: «Микрорайон Соломбала» names a place, not a
door (see `checks.yandex.vague_address`).
"""
from __future__ import annotations

import re

_DROP_PART = re.compile(r"^(?:архангельск|северодвинск|новодвинск|архангельская область|россия)$|округ\b|"
                        r"\bрайон\b|^исторический|этаж|офис|подъезд|помещение|вход\b|^\d{6}$")
_STREET_TYPES = frozenset({
    "улица", "ул", "проспект", "просп", "пр-т", "пр", "переулок", "пер", "площадь", "пл", "набережная", "наб",
    "шоссе", "ш", "проезд", "пр-д", "бульвар", "б-р", "тракт", "аллея", "линия", "тупик"})
_HOUSE = re.compile(r"^(\d+)\s*([а-яa-z])?(?:\s*(?:к|корп\.?|корпус)\s*(\d+))?"
                    r"(?:\s*(?:с|стр\.?|строение)\s*(\d+))?"
                    r"(?:\s*/\s*(\d+))?$")
_KORPUS = re.compile(r"^(?:к|корп|корпус)\.?\s*(\d+)$")
_BUILDING = re.compile(r"^(?:с|стр|строение)\.?\s*(\d+)$")


def _house(part: str, nxt: list[str]) -> str | None:
    m = _HOUSE.match(part.replace(" ", "") if re.fullmatch(r"[\d\sа-яa-z./]+", part) else part)
    if not m:
        return None
    num, letter, korp, bld, frac = m.groups()
    for p in nxt[:2]:  # «40, корп. 3», «20, стр. 1»
        if not korp and (k := _KORPUS.match(p)):
            korp = k.group(1)
        elif not bld and (b := _BUILDING.match(p)):
            bld = b.group(1)
    return (num + (letter or "") + (f"к{korp}" if korp else "") + (f"с{bld}" if bld else "")
            + (f"/{frac}" if frac else ""))


def address_key(text) -> tuple[frozenset[str], str] | None:
    """(street words, house) of one address, or None where it has no street and house."""
    if text is None or (isinstance(text, float) and text != text):
        return None
    s = str(text).lower().replace("ё", "е").split("·")[0].split("/архангельск")[0]
    parts = [p.strip() for p in s.split(",")]
    parts = [p for p in parts if p and not _DROP_PART.search(p)]
    for i, p in enumerate(parts):
        house = _house(p, parts[i + 1:])
        if house is None or i == 0:
            continue
        words = re.findall(r"[a-zа-я0-9]+", re.sub(r"(\d+)-?(?:й|я|е|го|ой|ая|ий)\b", r"\1", parts[i - 1]))
        street = frozenset(w for w in words if w not in _STREET_TYPES and len(w) > 1)
        if street:
            return street, house
    return None


def same_address(a, b) -> bool:
    """One street (the words of one contain the other's) and one house."""
    ka, kb = address_key(a), address_key(b)
    if ka is None or kb is None or ka[1] != kb[1]:
        return False
    return ka[0] <= kb[0] or kb[0] <= ka[0]


_SUFFIX = re.compile(r"(?:к\d+|с\d+)+$")


def same_building(a, b) -> bool:
    """`same_address`, or one street and one house where only one side names the korpus.

    The export writes «Красина, 3к1», the Yandex card «улица Красина, 3» for one
    door.  Formally «3» and «3, корп. 1» can be two buildings («Капитана
    Хромцова, 5» and «5к1» are), so a caller uses this only for points a few
    metres apart.  Two different korpus («5к1», «5к2») or letters («6», «6а»)
    are never one building.
    """
    ka, kb = address_key(a), address_key(b)
    if ka is None or kb is None or not (ka[0] <= kb[0] or kb[0] <= ka[0]):
        return False
    ha, hb = ka[1], kb[1]
    if ha == hb:
        return True
    if bool(_SUFFIX.search(ha)) == bool(_SUFFIX.search(hb)):
        return False
    return _SUFFIX.sub("", ha) == _SUFFIX.sub("", hb)
