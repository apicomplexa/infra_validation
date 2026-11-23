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
from pathlib import Path

import pandas as pd

CATEGORY_RU = {"medicine": "Медицинские организации", "grocery": "Продуктовые магазины",
               "education": "Образование", "sport": "Спортивные объекты",
               "catering": "Общепит", "alcohol": "Алкоголь", "tobacco": "Табак"}
# Independence groups as «Источники» names them -> the table's 0/1 column.
GROUPS = {"OSM": "OSM", "2GIS": "2GIS", "Яндекс": "Яндекс_проверка", "Overture": "Overture",
          "Foursquare": "Foursquare", "ЕГАИС": "ЕГАИС", "Росздравнадзор": "Росздравнадзор",
          "Администрация": "Администрация"}
KIND_WORDS = ["да", "нет", "противоречие", "автомат", "нет данных"]

VARIABLES = [
    ("ID", "номер объекта: поставка и «Объект (склеен)»"),
    ("Город", "город поставки"),
    ("Район", "район по выгрузке подрядчика (поле district первой записи объекта)"),
    ("Вид объекта", "класс поставки (медицина, продуктовые, образование, спорт и т. д.)"),
    ("Типы", "типы подрядчика у записей объекта, через «; »"),
    ("Название", "название первой записи объекта"),
    ("Адрес", "адрес первой записи объекта"),
    ("Широта", "координата первой записи объекта (WGS 84)"),
    ("Долгота", "координата первой записи объекта (WGS 84)"),
    ("OSM … Администрация", "1: группа источников засчитана хотя бы по одной записи объекта; 0: нет. "
                            "Только группы, проверявшиеся в поставке"),
    ("Источников", "число засчитанных независимых групп"),
    ("Подтверждено", "«Подтверждено (объект)»: не меньше двух независимых групп по объединённым "
                     "свидетельствам записей объекта"),
    ("Вид подтверждён", "«Вид?» по записям объекта: да, нет, противоречие, нет данных"),
    ("В базу", "1: подтверждено и «Вид подтверждён» не «нет»; это строки пилотной базы"),
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
        row = {"ID": f"{delivery}:{int(m)}", "Город": first["city"], "Район": first.get("district", ""),
               "Вид объекта": CATEGORY_RU.get(category, category),
               "Типы": "; ".join(sorted(set(d["type"].dropna().astype(str)))),
               "Название": first["name"], "Адрес": first["address"],
               "Широта": first["latitude"], "Долгота": first["longitude"]}
        for g, col in GROUPS.items():
            row[col] = int(g in src)
        row["Источников"] = len(src)
        row["Подтверждено"] = int(d["Подтверждено (объект)"].max())
        row["Вид подтверждён"] = fold_kind(d[kind_col])
        row["В базу"] = int(row["Подтверждено"] == 1 and row["Вид подтверждён"] != "нет")
        row["Записей"] = len(d)
        dates = d.get("Яндекс (наша): дата", pd.Series(dtype=str)).dropna().astype(str)
        row["Яндекс: дата проверки"] = max(dates) if len(dates) else ""
        rows.append(row)
    out = pd.DataFrame(rows)
    out.attrs["rows"] = len(df)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run", type=Path, required=True, help="results directory of the run")
    p.add_argument("--deliveries", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
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
                       "из них вид «нет»": int(((t["Подтверждено"] == 1) & (t["Вид подтверждён"] == "нет")).sum()),
                       "В базу": int(t["В базу"].sum())})
    base = pd.concat(parts, ignore_index=True)
    # Groups no delivery checked would be a column of zeros that reads as «not found».
    checked = [c for c in GROUPS.values() if base[c].any()]
    base = base.drop(columns=[c for c in GROUPS.values() if c not in checked])
    st = pd.DataFrame(stages)
    total = st.drop(columns=["Город"]).groupby("Вид объекта", sort=False).sum().reset_index()
    total.insert(0, "Город", "Все три города")
    st = pd.concat([st, total], ignore_index=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(a.out, engine="openpyxl") as xw:
        base.to_excel(xw, sheet_name="База", index=False)
        st.to_excel(xw, sheet_name="Этапы", index=False)
        pd.DataFrame(VARIABLES, columns=["Переменная", "Что значит"]).to_excel(xw, sheet_name="Переменные",
                                                                            index=False)
    print(f"wrote {a.out}: {len(base)} objects, {int(base['В базу'].sum())} in the base")
    print(st.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
