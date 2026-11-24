"""One table of objects across deliveries: the «единая сводная база».

Reads the decided workbooks a run wrote (`results/<run>/<delivery>/
*_подтверждено.xlsx`, sheet `data`) and keeps one row per merged object
(«Объект (склеен)»): where it is, what the contractor called it, which
independent groups confirmed it (0/1 each), the object-level verdict and the
object-level kind.  Nothing is re-decided here: every value is read from the
workbooks, so the table can be rebuilt from them at any time.

Yandex enters only as the 0/1 of its group: Yandex's own names, addresses and
links stay in the per-delivery workbooks (its terms forbid storing them; see
the project's decision D1).  The table carries 2GIS-derived rows and must not
be published while the 2GIS licence question is open.

    python workflow/scripts/unified_base.py --run results/finalest_nm \\
        --deliveries config/deliveries_finalest_nm.tsv --out results/finalest_nm/unified_base.xlsx
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

from infracheck.checks.yandex import search_url
from infracheck.core.address import address_key

CATEGORY_RU = {"medicine": "Медицинские организации", "grocery": "Продуктовые магазины",
               "education": "Образование", "sport": "Спортивные объекты",
               "catering": "Общепит", "alcohol": "Алкоголь", "tobacco": "Табак"}
# Independence groups as «Источники» names them -> the table's 0/1 column,
# named as in the supervisor's 2021 base (OSM, GIS, yandex, Arkh_Admin).
GROUPS = {"OSM": "OSM", "2GIS": "GIS", "Яндекс": "yandex", "Overture": "overture",
          "Foursquare": "fsq", "ЕГАИС": "egais", "Росздравнадзор": "roszdrav",
          "Администрация": "Arkh_Admin"}
KIND_WORDS = ["да", "нет", "противоречие", "автомат", "нет данных"]
# Outdoor places for physical activity: yards, squares, workout and ball
# courts, rinks, cycle tracks. Hardly any directory lists them, OSM does, so
# the two-group rule cannot confirm them; they enter the base on OSM alone
# (decision D26, 2026-11-24). Their «Подтверждено» stays the pipeline's own.
OUTDOOR_TYPES = {"Скверы и парковые зоны для физической активности", "Воркауты и уличные тренажеры",
                 "Ледовые коробки и катки", "Баскетбольные и волейбольные площадки", "Велодорожки"}
# An outdoor place enters on OSM alone only if it reads like one (the scan of
# 2026-11-24 found «Парковая улица», «Таксопарк», «ЖК Морепарк», «Знак в честь
# набережной», «Дорожный каток» and gyms filed as workouts in the base):
#   1. unnamed (the contractor wrote its type as the name): yes;
#   2. a bare street name («Парковая улица», «улица Победы»): no;
#   3. a name that opens with an outdoor word («Сквер у памятника», «Корт
#      теннисного клуба», «Велодорожка вдоль ул. Ленина»): yes;
#   4. a junk word anywhere (firm, housing, monument, roller, gym): no;
#   5. an outdoor word in the name or in a vague address (no house number:
#      the contractor's geocoder naming the spot, «Парк Зарусье»): yes.
OUTDOOR_START = re.compile(r"^(сквер|парк\b|площадк|спортивн\w* площадк|детск\w* площадк|корт|каток|коробк|"
                           r"ледов\w* (?:коробк|каток|площадк)|хоккейн|велодорож|лыж|стадион|набережн|аллея|"
                           r"бульвар|воркаут|workout|тренаж|турник|баскетбол|волейбол|футбол|памп|скейт|роллер|"
                           r"пляж|роща|зона отдыха)")
STREET_NAME = re.compile(r"^\S+\s+(?:улица|проспект|переулок|шоссе|проезд)$"
                         r"|^(?:улица|проспект|переулок|шоссе|проезд)\s+\S+$")
OUTDOOR_WORDS = re.compile(r"площадк|корт|воркаут|workout|тренаж|турник|каток|катк|коробк|сквер|парк\b|бульвар|"
                           r"аллея|набережн|велодорож|велосипед|лыж|стадион|поле\b|футбол|баскетбол|волейбол|"
                           r"хоккей|памп|скейт|роллер|пляж")
OUTDOOR_JUNK = re.compile(r"\bулица\b|\bул\.|проспект|переулок|жилой комплекс|\bжк\b|\bооо\b|\bип\b|компани|"
                          r"таксопарк|it-парк|технопарк|автопарк|парковк|стоянк|памятник|знак|скульптур|"
                          r"реконструкц|планируем|строящ|асфальт|дорожн|спортзал|\bзал\b|gym|fitness|фитнес|"
                          r"кроссфит|студи|клуб")
# Words that do not make an outdoor name a proper one: «Спортивная площадка»
# or «Детский парк» twice in a town are two places, «набережная Северной
# Двины» fifty-nine times is one.
OUTDOOR_COMMON = frozenset({
    "сквер", "парк", "площадка", "площадки", "спортивная", "спортивные", "детская", "детский", "детские",
    "игровая", "каток", "катки", "коробка", "ледовая", "хоккейная", "корт", "корты", "стадион", "поле",
    "футбольное", "футбольная", "баскетбольная", "волейбольная", "баскетбольные", "волейбольные", "воркаут",
    "воркаута", "workout", "тренажеры", "уличные", "турники", "велодорожка", "велодорожки", "лыжня", "лыжная",
    "трасса", "аллея", "бульвар", "набережная", "зона", "отдыха", "для", "и", "на", "с", "у", "в"})
RULE_TWO = "2+ независимых источника"
RULE_OUTDOOR = "городская среда: OSM"
# Why an object stays out of the base, first reason that applies.
OUT_SOURCES = "меньше двух источников"
OUT_KIND = "вид «нет»"
OUT_CLOSED = "Яндекс: больше не работает"
OUT_OUTDOOR_NAME = "уличный спорт: название не похоже на площадку или сквер"
OUT_REPEAT = "уличный спорт: повтор названия в городе (часть того же места)"
DUP_RADIUS_M = 30.0
# What a person should look at, first reason that applies (sheet «На ручную проверку»).
CHECK_CLOSED = "Яндекс пишет «Больше не работает»: закрыт ли объект"
CHECK_NEAR = "один источник, но в Яндексе по этому адресу (до 60 м) есть другая вывеска"
CHECK_CONFLICT = "в базе, но источники расходятся, относится ли объект к виду"
CHECK_OUTDOOR = "уличный спорт по одному OSM: есть ли площадка на месте"
NEAR_CHECK_M = 60.0
YANDEX_REGION = {"Архангельск": "20/arkhangelsk", "Северодвинск": "10849/severodvinsk", "Новодвинск": "20/arkhangelsk"}

HOUSE = re.compile(r",\s*\d")  # «улица, дом»: the export's address with a house number

VARIABLES = [
    ("ID", "номер объекта: поставка и «Объект (склеен)»"),
    ("Город", "город поставки"),
    ("Район", "район по выгрузке подрядчика (поле district первой записи объекта)"),
    ("Вид объекта", "класс поставки (медицина, продуктовые, образование, спорт и т. д.)"),
    ("Типы", "типы подрядчика у записей объекта, через «; »"),
    ("Название", "название с вывески: первая запись объекта, найденная в OSM, иначе первая запись"),
    ("Другие названия", "остальные названия записей объекта (2ГИС пишет вывеску с описанием: "
                        "«Пульс, многопрофильная клиника»), через «; »"),
    ("Адрес", "адрес записи с названием, если в нём есть номер дома, иначе первый адрес объекта с номером дома"),
    ("Широта", "координата записи с названием (WGS 84)"),
    ("Долгота", "координата записи с названием (WGS 84)"),
    ("OSM, GIS, yandex, overture, roszdrav…", "1: источник засчитан хотя бы по одной записи объекта "
                                               "(запись того же объекта, актуальная); 0: нет. Только "
                                               "источники, проверявшиеся хотя бы в одной поставке"),
    ("Источников", "число засчитанных независимых источников"),
    ("Подтверждено", "«Подтверждено (объект)»: не меньше двух независимых источников по объединённым "
                     "свидетельствам записей объекта"),
    ("Вид?", "относится ли объект к своему виду по описаниям источников (записи объекта вместе): "
             "да, нет, противоречие (источники расходятся), нет данных (описаний нет)"),
    ("Яндекс: больше не работает", "1: наш запрос нашёл эту вывеску в Яндекс Картах с пометкой "
                                   "«Больше не работает» (работающей карточки с тем же названием в 60 м нет) "
                                   "и другая карточка Яндекса за объект не засчитана; 0: нет или не запрашивался"),
    ("В базу", "1: строка пилотной базы: выполнено «Основание включения», «Вид?» не «нет», "
               "«Яндекс: больше не работает» = 0"),
    ("Основание включения", f"«{RULE_TWO}» или, для скверов, воркаутов, площадок, коробок и "
                            f"велодорожек, «{RULE_OUTDOOR}» (объект есть в OSM); пусто: не включён"),
    ("Почему не в базе", f"первая из причин: «{OUT_SOURCES}», «{OUT_OUTDOOR_NAME}», «{OUT_KIND}», "
                         f"«{OUT_CLOSED}», «{OUT_REPEAT}»; пусто: объект в базе"),
    ("Что проверить вручную", f"первая из причин: «{CHECK_CLOSED}», «{CHECK_NEAR}», «{CHECK_CONFLICT}», "
                              f"«{CHECK_OUTDOOR}»; пусто: не нужно"),
    ("Записей", "сколько строк выгрузки слились в объект"),
    ("Яндекс: дата проверки", "дата нашего запроса к Яндекс Картам (пусто: не запрашивался)"),
]


def fold_kind(words: pd.Series) -> str:
    """The object's kind from its records' kinds: «нет данных» abstains."""
    said = set(words.dropna().astype(str)) - {"", "нет данных"}
    if not said:
        return "нет данных"
    if "да" in said and "нет" in said:
        return "противоречие"
    for w in KIND_WORDS:
        if w in said:
            return w
    return sorted(said)[0]


def one_delivery(path: Path, delivery: str, category: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="data")
    kind_col = df.columns[2]  # front: Подтверждено, Основания, «Вид?»
    obj = "Объект (склеен)"
    df["_src"] = df["Источники"].fillna("").astype(str).str.split("; ")
    rows = []
    for m, d in df.groupby(obj, sort=True):
        first = d.iloc[0]
        src = {s for lst in d["_src"] for s in lst if s}
        # The name a reader looks for is the fascia: OSM's «Пульс», not the
        # contractor's 2GIS «Пульс, многопрофильная клиника» merged into it.
        osm = d[d["_src"].apply(lambda lst: "OSM" in lst)]
        named = osm.iloc[0] if len(osm) else first
        house = d["address"].astype(str).str.contains(r",\s*\d", regex=True)
        addr = (named["address"] if HOUSE.search(str(named["address"]))
                else d.loc[house, "address"].iloc[0] if house.any() else named["address"])
        others = sorted(set(d["name"].dropna().astype(str)) - {str(named["name"])})
        row = {"ID": f"{delivery}:{int(m)}", "Город": first["city"], "Район": first.get("district", ""),
               "Вид объекта": CATEGORY_RU.get(category, category),
               "Типы": "; ".join(sorted(set(d["type"].dropna().astype(str)))),
               "Название": named["name"], "Другие названия": "; ".join(others), "Адрес": addr,
               "Широта": named["latitude"], "Долгота": named["longitude"]}
        for g, col in GROUPS.items():
            row[col] = int(g in src)
        row["Источников"] = len(src)
        row["Подтверждено"] = int(d["Подтверждено (объект)"].max())
        row["Вид?"] = fold_kind(d[kind_col])
        ya = set(d.get("Яндекс (наша): статус", pd.Series(dtype=str)).dropna().astype(str))
        # Our query read this sign closed and no other Yandex card counts for the
        # object (the contractor's hit on that same card is withdrawn upstream;
        # one on another card keeps «yandex» = 1 and the object).
        closed = "closed" in ya and "present" not in ya and row["yandex"] == 0
        row["Яндекс: больше не работает"] = int(closed)
        types = set(d["type"].dropna().astype(str))
        outdoor = category == "sport" and types <= OUTDOOR_TYPES and "OSM" in src
        name_ok = outdoor_name_ok(str(named["name"]), str(addr), types)
        rule = (RULE_TWO if row["Подтверждено"] == 1 else RULE_OUTDOOR if outdoor and name_ok else "")
        why = (OUT_OUTDOOR_NAME if not rule and outdoor else OUT_SOURCES if not rule
               else OUT_KIND if row["Вид?"] == "нет" else OUT_CLOSED if closed else "")
        row["В базу"] = int(not why)
        row["Основание включения"] = rule if row["В базу"] else ""
        row["Почему не в базе"] = why
        near = d.get("Яндекс (наша): рядом", pd.Series(dtype=str)).dropna().astype(str)
        near_m = [float(m) for t in near for m in re.findall(r" — (\d+(?:\.\d+)?) м", t)]
        row["Что проверить вручную"] = (
            CHECK_CLOSED if why == OUT_CLOSED
            else CHECK_NEAR if why == OUT_SOURCES and any(m <= NEAR_CHECK_M for m in near_m)
            else CHECK_CONFLICT if row["В базу"] and row["Вид?"] == "противоречие"
            else CHECK_OUTDOOR if row["В базу"] and rule == RULE_OUTDOOR and row["Источников"] == 1
            else "")
        row["Записей"] = len(d)
        dates = d.get("Яндекс (наша): дата", pd.Series(dtype=str)).dropna().astype(str)
        row["Яндекс: дата проверки"] = max(dates) if len(dates) else ""
        rows.append(row)
    out = pd.DataFrame(rows)
    # OSM splits a promenade or a park into many ways: one named outdoor place per city
    # («набережная Северной Двины» came as 59 segments).  Unnamed courts stay apart.
    if len(out):
        key = out["Название"].astype(str).str.lower().str.replace("ё", "е").str.split().str.join(" ")
        proper = key.map(lambda k: any(w not in OUTDOOR_COMMON for w in re.findall(r"[a-zа-я]+", k)))
        outdoor = out["Типы"].astype(str).str.split("; ").map(lambda t: set(t) <= OUTDOOR_TYPES)
        named = ~out["Название"].astype(str).str.strip().isin(OUTDOOR_TYPES)  # unnamed courts stay apart
        pool = out["В базу"].eq(1) & named & proper & outdoor   # included named outdoor places, any rule
        # keep one per name and city: a two-source object if there is one, else the first
        cand = sorted(out.index[pool], key=lambda i: (out.at[i, "Основание включения"] != RULE_TWO, i))
        kept, seen = set(), set()
        for i in cand:
            if key[i] not in seen:
                seen.add(key[i])
                kept.add(i)
        rep = pool & ~out.index.isin(list(kept)) & out["Основание включения"].eq(RULE_OUTDOOR)
        cols = ["В базу", "Основание включения", "Почему не в базе", "Что проверить вручную"]
        out.loc[rep, cols] = [0, "", OUT_REPEAT, ""]
    out.attrs["rows"] = len(df)
    return out


def outdoor_name_ok(name: str, address: str, types: set[str]) -> bool:
    """May this OSM-only outdoor object enter the base (see OUTDOOR_WORDS)?"""
    if name.strip() in types:
        return True
    low = " ".join(name.lower().replace("ё", "е").split())
    if STREET_NAME.match(low):
        return False
    if OUTDOOR_START.match(low):
        return True
    if OUTDOOR_JUNK.search(low):
        return False
    vague = address.lower() if address and address != "nan" and not re.search(r",\s*\d", address) else ""
    return bool(OUTDOOR_WORDS.search(low) or OUTDOOR_WORDS.search(vague))


def possible_duplicates(base: pd.DataFrame) -> pd.DataFrame:
    """Pairs that may be one place the merge left apart: a manual-review list.

    One object only in OSM and one only in 2GIS, of a shared contractor type,
    within 30 m, but with names the pipeline did not find alike («Аптека
    No 2» / «Фармация, аптека No2»). Most such pairs are neighbours (two
    pharmacies in one building), which is why they are listed, not merged.
    """
    rows = []
    for (city, kind), g in base.groupby(["Город", "Вид объекта"], sort=False):
        g = g.reset_index(drop=True)
        lat, lon = np.radians(g["Широта"].to_numpy(float)), np.radians(g["Долгота"].to_numpy(float))
        osm_only = np.flatnonzero((g["OSM"] == 1) & (g["GIS"] == 0))
        gis_only = np.flatnonzero((g["OSM"] == 0) & (g["GIS"] == 1))
        types = [set(str(t).split("; ")) for t in g["Типы"]]
        for i in osm_only:
            h = (np.sin((lat[gis_only] - lat[i]) / 2) ** 2
                 + np.cos(lat[i]) * np.cos(lat[gis_only]) * np.sin((lon[gis_only] - lon[i]) / 2) ** 2)
            d = 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
            for j, dist in zip(gis_only, d, strict=True):
                if dist <= DUP_RADIUS_M and types[i] & types[j]:
                    rows.append({"Город": city, "Вид объекта": kind, "Расстояние, м": round(float(dist)),
                                 "ID (OSM)": g.at[i, "ID"], "Название (OSM)": g.at[i, "Название"],
                                 "Адрес (OSM)": g.at[i, "Адрес"], "ID (2GIS)": g.at[j, "ID"],
                                 "Название (2GIS)": g.at[j, "Название"], "Адрес (2GIS)": g.at[j, "Адрес"]})
    return pd.DataFrame(rows)


def address_order(df: pd.DataFrame) -> pd.DataFrame:
    """Sorted by city, street, house: one walk along each street (the «На ручную проверку» sheet)."""
    keys = df["Адрес"].map(address_key)
    street = [(" ".join(sorted(k[0])) if k else str(a).lower()) for k, a in zip(keys, df["Адрес"], strict=True)]
    house = [int(re.match(r"\d+", k[1]).group()) if k else 10**6 for k in keys]
    return (df.assign(_s=street, _h=house).sort_values(["Город", "_s", "_h", "Название"])
            .drop(columns=["_s", "_h"]))


def write_books(base: pd.DataFrame, st: pd.DataFrame, dups: pd.DataFrame, folder: Path, stamp: str) -> list[Path]:
    """One workbook per category, the layout of the 2021 base's appendices.

    Sheets: «База» (the pilot base), «Все объекты» (with «Почему не в базе»),
    «На ручную проверку» (sorted by address, with the search link a person
    opens: our own query text, not Yandex's data), «Этапы», «Возможные дубли»,
    «Переменные».
    """
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for kind, g in base.groupby("Вид объекта", sort=False):
        f = folder / f"{stamp} {kind}.xlsx"
        rv = address_order(g[g["Что проверить вручную"].ne("")]).copy()
        rv.insert(0, "Что проверить вручную", rv.pop("Что проверить вручную"))
        rv["Ссылка для проверки"] = [
            search_url(str(n), "" if str(a) == "nan" else str(a), float(la), float(lo), str(c),
                       YANDEX_REGION.get(str(c), "20/arkhangelsk"))
            for n, a, la, lo, c in zip(rv["Название"], rv["Адрес"], rv["Широта"], rv["Долгота"], rv["Город"],
                                       strict=True)]
        rv["Решение (да / нет / не ясно)"] = ""
        rv["Комментарий"] = ""
        with pd.ExcelWriter(f, engine="openpyxl") as xw:
            g[g["В базу"].eq(1)].to_excel(xw, sheet_name="База", index=False)
            g.to_excel(xw, sheet_name="Все объекты", index=False)
            rv.to_excel(xw, sheet_name="На ручную проверку", index=False)
            st[st["Вид объекта"].eq(kind)].to_excel(xw, sheet_name="Этапы", index=False)
            dups[dups["Вид объекта"].eq(kind)].to_excel(xw, sheet_name="Возможные дубли", index=False) \
                if len(dups) else pd.DataFrame().to_excel(xw, sheet_name="Возможные дубли", index=False)
            pd.DataFrame(VARIABLES, columns=["Переменная", "Что значит"]).to_excel(xw, sheet_name="Переменные",
                                                                                index=False)
        out.append(f)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run", type=Path, required=True, help="results directory of the run")
    p.add_argument("--deliveries", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--books", type=Path, help="folder for one workbook per category")
    p.add_argument("--stamp", default="", help="file name prefix of the books, e.g. 261125")
    a = p.parse_args(argv)
    dl = pd.read_csv(a.deliveries, sep="\t")
    parts, stages = [], []
    for r in dl.itertuples():
        files = sorted((a.run / r.delivery).glob("*_подтверждено.xlsx"))
        if len(files) != 1:
            raise SystemExit(f"{r.delivery}: expected one decided workbook, found {len(files)}")
        t = one_delivery(files[0], r.delivery, r.category)
        parts.append(t)
        stages.append({"Город": r.city, "Вид объекта": CATEGORY_RU.get(r.category, r.category),
                       "Строк в выгрузке": t.attrs["rows"], "Объектов после склейки": len(t),
                       "Подтверждено (2+ источника)": int(t["Подтверждено"].sum()),
                       "из них вид «нет»": int(((t["Подтверждено"] == 1) & (t["Вид?"] == "нет")).sum()),
                       "Городская среда по OSM": int((t["Основание включения"] == RULE_OUTDOOR).sum()),
                       "Меньше двух источников": int((t["Почему не в базе"] == OUT_SOURCES).sum()),
                       "Исключено: Яндекс «Больше не работает»": int((t["Почему не в базе"] == OUT_CLOSED).sum()),
                       "Уличный спорт: не площадка или повтор": int(t["Почему не в базе"].isin(
                           [OUT_OUTDOOR_NAME, OUT_REPEAT]).sum()),
                       "В базу": int(t["В базу"].sum())})
    base = pd.concat(parts, ignore_index=True)
    # Groups no delivery checked would be a column of zeros that reads as «not found».
    checked = [c for c in GROUPS.values() if base[c].any()]
    base = base.drop(columns=[c for c in GROUPS.values() if c not in checked])
    st = pd.DataFrame(stages)
    total = st.drop(columns=["Город"]).groupby("Вид объекта", sort=False).sum().reset_index()
    total.insert(0, "Город", "Все три города")
    st = pd.concat([st, total], ignore_index=True)
    dups = possible_duplicates(base)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(a.out, engine="openpyxl") as xw:
        base.to_excel(xw, sheet_name="База", index=False)
        st.to_excel(xw, sheet_name="Этапы", index=False)
        dups.to_excel(xw, sheet_name="Возможные дубли", index=False)
        pd.DataFrame(VARIABLES, columns=["Переменная", "Что значит"]).to_excel(xw, sheet_name="Переменные",
                                                                            index=False)
    if a.books:
        for f in write_books(base, st, dups, a.books, a.stamp or "база"):
            print(f"wrote {f}")
    print(f"wrote {a.out}: {len(base)} objects, {int(base['В базу'].sum())} in the base")
    print(st.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
