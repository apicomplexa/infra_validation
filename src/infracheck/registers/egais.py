"""The FSRAR consolidated licence registry: streaming parse, loader, RegisterTable.

Three layers, each ported from the archive repository (tag `archive-2026-10-30`:
`scripts/parse_egais.py`, `arkhinfra.egais`, `arkhinfra.final_egais`):

* `parse()` reduces the published ~10.9 GB XML to the rows of a few
  settlements, written as a 27-column TSV.  It never holds the file in memory:
  the file is read in binary chunks and cut on the ``</row>`` boundary, and only
  the segments whose raw bytes already mention one of the settlements are handed
  to the XML parser.  That is a memchr-speed substring test over the whole file
  and a real parse over a few thousand records.
* `load()` types that TSV: Russian dates, the status and activity vocabularies,
  coordinates kept only inside the settlement's box, the city and the venue name
  the registry writes into the premises text.
* `to_register()` adapts the loaded frame to the `licmatch` RegisterTable
  contract, which is all `licmatch.match()` ever sees of this source.

Note the published data does *not* follow its own XSD: the schema describes
``Details/Detail`` with attributes, the file delivers ``data/row`` with child
elements, and several element names differ from the schema (the registry's own
"выезного" typo among them).  The map below follows the file.  A parser written
from the documentation returns nothing.

Two things about this source govern how it may be used:

* **It holds alcohol licences, not tobacco.**  The regulator covers both, the
  data has no tobacco class at all.
* **It is a register, not a list of open outlets.**  Only 585 of 2,100
  Arkhangelsk rows are in force.  Always filter `status` / `is_active`.

    python -m infracheck.registers.egais parse --zip resources/egais/data-….zip \\
        --settlements архангельск северодвинск новодвинск --out data/temp/egais/egais_licences_final.tsv
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import licmatch as lm
import pandas as pd

# --------------------------------------------------------------------------
# the streaming parse
# --------------------------------------------------------------------------
CHUNK = 16 << 20  # 16 MiB

# Element name in the file -> column name in the TSV.  Anything not listed is
# dropped; История_изменений_сведений_о_действии_лицензии is the big one, and
# dropping it is most of what makes the output compact.
FIELDS = {  # noqa: E501 -- the element names are the file's, unbroken
    "Полное_и_сокращенное_наименование_организации_сельскохозяйственного_товаропроизводителя_с_указанием_ее_ОПФ": "org_name",  # noqa: E501
    "ИНН_организации_сельскохозяйственного_товаропроизводителя_": "org_inn",
    "КПП_организации_сельскохозяйственного_товаропроизводителя_": "org_kpp",
    "Адрес__место_нахождения___организации_сельскохозяйственного_товаропроизводителя_": "org_address",
    "Адрес_электронной_почты_организации_сельскохозяйственного_товаропроизводителя_": "org_email",
    "Место_нахождения__адрес__обособленного_подразделения_организации__осуществляющего_лицензируемый_вид_деятельности": "outlet_address",  # noqa: E501
    "КПП_обособленного_подразделения_организации__осуществляющего_лицензируемый_вид_деятельности": "outlet_kpp",
    "Код_субъекта_Российской_Федерации_по_месту_нахождения_организации": "org_region_code",
    "Код_субъекта_РФ_по_месту_нахождения_обособленного_подразделения_организации__осуществляющего_лицензируемый_вид_деятельности": "outlet_region_code",  # noqa: E501
    "Вид_лицензируемой_деятельности_организации": "activity",
    "Виды_продукции": "products",
    "Номер_лицензии__соответствующий_номеру_записи_в_реестре": "licence_no",
    "Номер_ранее_выданной_лицензии": "licence_prev_no",
    "Дата_выдачи_лицензии": "date_issued",
    "Дата_окончания_действия_лицензии": "date_expires",
    "Сведения_о_действии_лицензии": "status",
    "Дата_изменения_сведений_о_действии_лицензии": "status_changed",
    "Основание_изменения_сведений_о_действии_лицензии": "status_reason",
    "Орган_выдавший_лицензию": "issuing_body",
    "Координаты": "coords_raw",
}

# Present in the file, deliberately not carried over.
IGNORED = {
    "История_изменений_сведений_о_действии_лицензии",
    "Номер_бланка",
    "Дата__время_и_место_выезного_обслуживания",
    "Дата__время_и_место_выездного_обслуживания",
    "Уточненные_сведения_о_выездном_обслуживании",
    "Дата_внесения_изменений_в_сведения_о_лицензии_по_судебным_актам_и_актам_других_органов",
}

COLUMNS = [
    "licence_no", "licence_prev_no", "status", "date_issued", "date_expires",
    "status_changed", "status_reason", "activity", "products",
    "org_name", "org_inn", "org_kpp", "org_email", "org_address",
    "org_region_code", "outlet_region_code", "outlet_address",
    "outlet_settlement", "outlet_subsettlement", "outlet_street",
    "outlet_house", "outlet_kpp", "lat", "lon", "coord_plausible",
    "coords_raw", "issuing_body",
]

# Generous boxes around each settlement, used only to flag the registry's own
# coordinate typos (dropped degrees, transposed digits) -- the values are kept
# either way.  Each row is checked against the box of its own settlement; a
# settlement missing here gets no `coord_plausible` verdict, and `load()` then
# drops its coordinate.
BOXES = {
    "архангельск": (64.40, 64.75, 40.30, 40.90),  # lat_min, lat_max, lon_min, lon_max
    "северодвинск": (64.45, 64.70, 39.40, 40.20),
    "новодвинск": (64.35, 64.47, 40.65, 41.00),
}

# A settlement token as the registry writes it: "Архангельск Город", "Талаги
# Деревня", occasionally "г Архангельск".  Matching whole tokens is what keeps
# "Архангельская Область" out of a city filter.
_TYPES = r"город|г|пгт|рп|пос|поселок|посёлок|п|село|с|деревня|д|станция|ст|снт|мкр"
_SETTLEMENT = re.compile(
    rf"^(?:(?P<t1>{_TYPES})\.?\s+(?P<n1>.+)|(?P<n2>.+?)\s+(?P<t2>{_TYPES})\.?)$", re.I
)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
# Region-level words, so a region never gets read as the settlement.
_REGIONISH = re.compile(r"\b(обл|область|край|респ|республика|ао|округ\s+автономный|автономный)\b", re.I)
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def settlement_name(token: str) -> str:
    """"Архангельск Город" -> "архангельск"; anything unparsed comes back as-is."""
    m = _SETTLEMENT.match(token.strip())
    if not m:
        return token.strip().lower()
    return (m.group("n1") or m.group("n2") or "").strip().lower()


def split_address(addr: str) -> tuple[str, str, str, str]:
    """(settlement, subsettlement, street, house) from the comma-packed address.

    The layout is positional and stable across the registry:

        0 country, 2 region, 4 settlement, 5 subsettlement, 6 street, 7 house

    with the tail (8+) holding free-form premises text.  Position is what a bare
    "Архангельск" needs — it carries no type word, so a type-word search walks
    past it and latches onto the house number "д. 120" instead.  The type-word
    search stays as the fallback for the rare row with a shifted layout.
    """
    parts = [p.strip() for p in addr.split(",")]
    if len(parts) > 4 and parts[4] and not _REGIONISH.search(parts[4]):
        sub = parts[5] if len(parts) > 5 else ""
        street = parts[6] if len(parts) > 6 else ""
        house = parts[7] if len(parts) > 7 else ""
        return parts[4], sub, street, house

    # Shifted layout (a region word sitting in the settlement slot, or an empty
    # one): fall back to hunting the type word.  The name must contain a letter,
    # or the single-letter types д/с/п match a house number such as "д. 120".
    for i, p in enumerate(parts):
        if not p or p.lower() == "россия" or _REGIONISH.search(p):
            continue
        m = _SETTLEMENT.match(p)
        name = (m.group("n1") or m.group("n2") or "") if m else ""
        if name and _HAS_LETTER.search(name):
            rest = [q for q in parts[i + 1:] if q]
            return p, "", (rest[0] if rest else ""), (rest[1] if len(rest) > 1 else "")
    return "", "", "", ""


def needles_for(names: list[str]) -> tuple[bytes, ...]:
    """Byte needles for a settlement list.

    The first letter is dropped so one needle catches the capitalised and the
    lower-case spelling, and an upper-case one catches the shouted form:
    "рхангельск" / "РХАНГЕЛЬСК" catch "Архангельск", "архангельск" and
    "АРХАНГЕЛЬСК" alike.
    """
    out: list[bytes] = []
    for n in names:
        stem = n.strip().lower()[1:]
        out += [stem.encode(), stem.upper().encode()]
    return tuple(out)


def open_source(src: Path):
    """(binary stream, uncompressed size) for the XML or a ZIP holding it.

    A member of a ZIP is read through `zipfile`'s decompressing stream, so the
    archive never has to be unpacked to disk, at the cost of ~1 minute of inflate.
    """
    if src.suffix.lower() != ".zip":
        return src.open("rb"), src.stat().st_size
    zf = zipfile.ZipFile(src)
    xml = [i for i in zf.infolist() if i.filename.lower().endswith(".xml")]
    if len(xml) != 1:
        raise ValueError(f"{src}: expected one .xml member, found {len(xml)}")
    return zf.open(xml[0]), xml[0].file_size


def parse_coords(raw: str) -> tuple[str, str]:
    """"Широта - 64° 32' 22''; Долгота - 40° 32' 5''" -> ("64.539444", "40.534722")."""
    if not raw or "Широта" not in raw:
        return "", ""
    lat_part, _, lon_part = raw.partition(";")
    out = []
    for part in (lat_part, lon_part):
        nums = [float(n.replace(",", ".")) for n in _NUM.findall(part)]
        if not nums:
            out.append("")
            continue
        deg = nums[0]
        if len(nums) >= 3:
            deg += nums[1] / 60 + nums[2] / 3600
        elif len(nums) == 2:
            deg += nums[1] / 60
        out.append(f"{deg:.6f}")
    return out[0], (out[1] if len(out) > 1 else "")


def clean(v: str | None) -> str:
    """TSV-safe: no tabs, no newlines, no doubled spaces."""
    return " ".join((v or "").split())


def row_to_record(xml: bytes, unknown: set[str]) -> dict[str, str]:
    rec: dict[str, str] = {}
    for child in ET.fromstring(xml):
        col = FIELDS.get(child.tag)
        if col is None:
            if child.tag not in IGNORED:
                unknown.add(child.tag)
            continue
        val = clean(child.text)
        # A few tags repeat inside one row (licence_prev_no does); first
        # non-empty value wins.
        if val and not rec.get(col):
            rec[col] = val
    return rec


def parse(src: Path, settlements: list[str], out: Path, limit: int = 0) -> int:
    """Stream the registry and write the rows of `settlements` to `out` (TSV).

    A row is kept when the settlement slot of its outlet address names one of
    the settlements -- checked on that slot rather than the whole string, so
    neither "Архангельская Область" nor a street called "Архангельская" can pull
    a record in.  ``outlet_region_code`` is *not* used: the registry leaves it
    wrong on ~4 % of Arkhangelsk rows.  Returns the number of rows written.
    """
    keep_names = {s.strip().lower() for s in settlements}
    needles = needles_for(sorted(keep_names))
    out.parent.mkdir(parents=True, exist_ok=True)

    def keep(rec: dict[str, str]) -> bool:
        return settlement_name(split_address(rec.get("outlet_address", ""))[0]) in keep_names

    fh, total = open_source(src)
    unknown: set[str] = set()
    records: list[dict[str, str]] = []
    n_rows = n_hits = read = next_report = 0
    buf = b""

    with fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            read += len(chunk)
            segments = (buf + chunk).split(b"</row>")
            buf = segments.pop()
            for seg in segments:
                n_rows += 1
                if not any(n in seg for n in needles):
                    continue
                start = seg.find(b"<row>")
                if start < 0:
                    continue
                n_hits += 1
                rec = row_to_record(seg[start:] + b"</row>", unknown)
                if keep(rec):
                    records.append(rec)
            if read >= next_report:
                print(f"  {read / 1e9:6.2f} / {total / 1e9:.2f} GB "
                      f"({100 * read / total:5.1f}%)  rows {n_rows:,}  "
                      f"hits {n_hits:,}  kept {len(records):,}",
                      file=sys.stderr, flush=True)
                next_report = read + (1 << 30)
            if limit and len(records) >= limit:
                break

    for rec in records:
        addr = rec.get("outlet_address", "")
        (rec["outlet_settlement"], rec["outlet_subsettlement"],
         rec["outlet_street"], rec["outlet_house"]) = split_address(addr)
        lat, lon = parse_coords(rec.get("coords_raw", ""))
        rec["lat"], rec["lon"] = lat, lon
        box = BOXES.get(settlement_name(rec["outlet_settlement"]))
        if lat and lon and box:
            lo_a, hi_a, lo_o, hi_o = box
            rec["coord_plausible"] = str(lo_a <= float(lat) <= hi_a and lo_o <= float(lon) <= hi_o).lower()

    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        for rec in records:
            fh.write("\t".join(rec.get(c, "") for c in COLUMNS) + "\n")

    print(f"\nrows scanned        {n_rows:,}", file=sys.stderr)
    print(f"needle hits         {n_hits:,}", file=sys.stderr)
    print(f"written (settlements {','.join(sorted(keep_names))})  {len(records):,} -> {out}",
          file=sys.stderr)
    if unknown:
        print("unmapped elements (dropped): " + ", ".join(sorted(unknown)), file=sys.stderr)
    return len(records)


# --------------------------------------------------------------------------
# the loader
# --------------------------------------------------------------------------
# Registry dates are "05 дек 2012" -- a Russian three-letter month that no
# locale-independent parser recognises.
_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
_DATE = re.compile(r"^\s*(\d{1,2})\s+([а-я]{3})[а-я]*\.?\s+(\d{4})\s*$", re.I)

# The registry writes the same expiry state two ways, and the same ministry ten.
STATUS_EN = {
    "действующая": "Active",
    "истекла": "Expired",
    "срок действия истек": "Expired",
    "прекращена": "Terminated",
    "переоформлена": "Reissued",
    "аннулирована": "Annulled",
}
STATUS_ORDER = ["Active", "Expired", "Terminated", "Reissued", "Annulled"]

# Licensed activity folded onto five classes.  Only the two retail classes put
# alcohol in front of a resident; the rest are logistics and production.
ACTIVITY_EN = {
    "retail": "Retail (off-trade)",
    "catering": "Retail + on-trade",
    "wholesale": "Wholesale & storage",
    "transport": "Ethanol transport",
    "production": "Production",
}
ACTIVITY_ORDER = ["Retail (off-trade)", "Retail + on-trade", "Wholesale & storage",
                  "Ethanol transport", "Production"]
CATERING = ACTIVITY_EN["catering"]
RETAIL = ACTIVITY_EN["retail"]

# Belushya Guba and Rogachevo are on Novaya Zemlya, ~2,000 km north of the city,
# yet the registry files them under "Архангельск г".
NOVAYA_ZEMLYA = ("белушья", "рогачево")


def parse_ru_date(v) -> pd.Timestamp | pd.NaT:
    """"05 дек 2012" -> Timestamp. ISO strings pass through pandas unchanged."""
    if not isinstance(v, str) or not v.strip():
        return pd.NaT
    m = _DATE.match(v)
    if m:
        day, mon, year = m.group(1), m.group(2).lower(), m.group(3)
        if mon in _MONTHS:
            return pd.Timestamp(int(year), _MONTHS[mon], int(day))
    return pd.to_datetime(v, errors="coerce", dayfirst=True)


def classify_activity(v) -> str:
    s = str(v).lower()
    if "перевозк" in s:
        return ACTIVITY_EN["transport"]
    if "производств" in s:
        return ACTIVITY_EN["production"]
    if "закупка" in s or "поставки" in s:
        return ACTIVITY_EN["wholesale"]
    if "общепит" in s:
        return ACTIVITY_EN["catering"]
    return ACTIVITY_EN["retail"]


# A venue named in the registry's premises text: quoted after the venue word or
# anywhere, or a capitalised name right after it («кафе Караван (пом. 4)»).
_VENUE = r"(?:лобби-бар|гастробар|кафе|ресторан|бар|паб|буфет|столовая|кофейня|пиццерия|закусочная|трактир)"
_QUOTED = re.compile(r"[\"«“]([^\"»”]{2,60})[\"»”]")
_BARE = re.compile(_VENUE + r"\s+([A-ZА-ЯЁ0-9][^,($)]{1,40})")
# Municipality boilerplate that trails a bare name: "... м.о. Город Северодвинск".
_TAIL_NOISE = re.compile(r"\s+(?:г\.?\s?о\.?|м\.?\s?о\.?|го|городской округ)\b.*$", re.I)


def premises_text(addr: str) -> str:
    """The free-form tail of the registry's comma-packed outlet address (slot 10+)."""
    parts = str(addr).split(",")
    return ",".join(parts[10:]).replace("&quot$", '"').replace("&quot;", '"')


def _not_municipality(name: str) -> bool:
    return not re.match(r"(?:город|г\.|городской|м\.?о|г\.?о)\b", name.strip(), re.I)


def premises_venue(addr: str) -> str:
    """The venue name the registry writes into the premises text, or "".

    'часть пом. 22, 1 этаж, кафе Трактир' -> 'Трактир';
    'ресторан "Йоко" (пом. 9, 1 этаж)' -> 'Йоко'.  A quoted name wins over a
    bare one; neither is guessed when the text names only the room.
    """
    tail = premises_text(addr)
    # A quoted "Город Северодвинск" is the municipality, not a venue.
    quoted = [q.strip() for q in _QUOTED.findall(tail) if _not_municipality(q)]
    if quoted:
        return quoted[0]
    m = _BARE.search(tail)
    if m:
        return _TAIL_NOISE.sub("", m.group(1)).strip()
    return ""


def load(path) -> pd.DataFrame:
    """A `parse()` extract, typed and normalised, one row per registry row.

    Adds `status_en`, `is_active`, `activity_class`, `is_retail`, `latitude` /
    `longitude` (only where the coordinate is plausible), `has_coords`,
    `off_mainland`, `issue_year`, and for the matchers `city_key` (the folded
    first word of the outlet settlement: "архангельск"), `venue` (the premises
    venue name) and `inn` (the holder's tax number as text).
    """
    df = pd.read_csv(path, sep="\t", dtype=str).fillna("")

    for col in ("date_issued", "date_expires", "status_changed"):
        df[col] = df[col].map(parse_ru_date)

    df["status_en"] = df["status"].str.strip().str.lower().map(STATUS_EN).fillna("Other")
    df["is_active"] = df["status_en"] == "Active"
    df["activity_class"] = df["activity"].map(classify_activity)
    df["is_retail"] = df["activity_class"].isin(
        [ACTIVITY_EN["retail"], ACTIVITY_EN["catering"]])

    # Coordinates: keep only the ones inside the settlement's box.  The rest are
    # the registry's own typos, and a map is the wrong place to argue with them.
    lat = pd.to_numeric(df["lat"], errors="coerce")
    lon = pd.to_numeric(df["lon"], errors="coerce")
    ok = df["coord_plausible"].eq("true")
    df["latitude"] = lat.where(ok)
    df["longitude"] = lon.where(ok)
    df["has_coords"] = df["latitude"].notna()

    sub = df["outlet_subsettlement"].str.lower()
    df["off_mainland"] = sub.str.contains("|".join(NOVAYA_ZEMLYA), na=False)
    df["issue_year"] = df["date_issued"].dt.year

    df["city_key"] = df["outlet_settlement"].map(
        lambda s: lm.fold(s).split()[0] if str(s).strip() else "")
    df["venue"] = df["outlet_address"].map(premises_venue)
    df["inn"] = org_inn(df)
    return df.reset_index(drop=True)


def org_inn(eg: pd.DataFrame) -> pd.Series:
    """The holder's tax number as text -- the identity a learned pair names.

    Two unrelated companies can both be ООО "Ритейл"; only the INN tells them
    apart.
    """
    return (eg["org_inn"].astype(str).str.replace(r"\.0$", "", regex=True)
            .replace({"nan": "", "<NA>": ""}))


def blocks(addr: pd.Series) -> pd.Series:
    """The corpus slot (position 8) of the registry's comma-packed address.

    `outlet_street` / `outlet_house` come out of `parse()`; the corpus sits one
    slot further along the comma-packed `outlet_address`.
    """
    return addr.fillna("").astype(str).str.split(",").map(
        lambda p: p[8].strip() if len(p) > 8 else "")


# --------------------------------------------------------------------------
# the RegisterTable adapter
# --------------------------------------------------------------------------
# The registry converts degrees-minutes-seconds and rounds to the arcsecond:
# ~31 m of latitude at this latitude.  Carried for the reader; licmatch does
# not use it.
COORD_PRECISION_M = 31.0


def to_register(eg: pd.DataFrame) -> pd.DataFrame:
    """`load()`'s frame as a licmatch RegisterTable, the registry columns kept.

    One row per registry row, in the same order (licmatch addresses rows by
    position).  `record_id` is the licence number -- *not* unique: the registry
    files one licence once per status change, and a chain's single number covers
    dozens of addresses.  `holder_id` is the INN, `venue_name` the premises
    venue, `kind` the activity class, `block` the city key.  The registry's own
    columns follow the canonical ones, for the adapters that write them out.
    """
    reg = pd.DataFrame({
        "record_id": eg["licence_no"].astype(str),
        "holder_id": org_inn(eg),
        "holder_name": eg["org_name"].astype(str),
        "venue_name": eg["venue"].astype(str) if "venue" in eg
        else eg["outlet_address"].map(premises_venue),
        "address_line": eg["outlet_address"].fillna("").astype(str),
        "settlement": eg["outlet_subsettlement"].astype(str),
        "street": eg["outlet_street"].astype(str),
        "house": eg["outlet_house"].astype(str),
        "block": blocks(eg["outlet_address"]),
        "lat": eg["latitude"],
        "lon": eg["longitude"],
        "coord_precision_m": COORD_PRECISION_M,
        "in_force": eg["is_active"].astype(bool),
        "valid_from": eg["date_issued"],
        "status_changed": eg["status_changed"],
        "kind": eg["activity_class"].astype(str),
        "city": eg["city_key"].astype(str) if "city_key" in eg else "",
    })
    extra = eg.drop(columns=[c for c in eg.columns if c in reg.columns])
    return pd.concat([reg, extra.reset_index(drop=True)], axis=1)


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m infracheck.registers.egais",
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="stream the registry ZIP into a settlements TSV")
    p.add_argument("--zip", type=Path, required=True,
                   help="the registry ZIP (or the unpacked XML)")
    p.add_argument("--settlements", nargs="+", required=True, metavar="NAME",
                   help="keep rows whose outlet settlement is one of these "
                        "(lower case, e.g. архангельск северодвинск)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=0, help="stop after N kept rows (smoke test)")
    args = ap.parse_args(argv)
    if not args.zip.exists():
        raise FileNotFoundError(args.zip)
    parse(args.zip, args.settlements, args.out, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
