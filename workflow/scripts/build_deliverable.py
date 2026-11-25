"""Assemble the supervisor's folder `results/finalest/deliverable/` (`pixi run deliverable`).

Nothing here decides anything: the nine workbooks are copied as the pipeline
wrote them, and every number of the summary table is read from the per-delivery
`summary.txt` the pipeline wrote beside each workbook (the same block as in
`results/finalest/<category>_summary.txt`). The script refuses to build when a
workbook lacks its legend sheet or when the summary's own numbers do not
reconcile (sources pooled per object against the confirmed count), so a stale
or partial `results/finalest/` cannot turn into a deliverable.

Evidence kinds are never added up: each source group gets its own column, the
evidence that was found but not counted goes to a separate sheet, and the kind
verdicts («Общепит?» / «Алкоголь?» / «Табак?») stand apart from existence.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FINALEST = ROOT / "results" / "finalest"
OUT = FINALEST / "deliverable"
METHODOLOGY = ROOT / "docs" / "methodology_finalest.md"
MERGE_REVIEW = ROOT / "results" / "finalest" / "calibration" / "diagnostics" / "osm_gis_merge.csv"
IV_EVAL = ROOT / "results" / "finalest" / "internal_validity" / "eval"
DELIVERIES = ROOT / "config" / "deliveries_finalest.tsv"

CITY_ORDER = ["Архангельск", "Северодвинск", "Новодвинск"]
CATEGORY = {"catering": "общепит", "alcohol": "алкоголь", "tobacco": "табак"}
KIND_LABEL = {"catering": "Общепит?", "alcohol": "Алкоголь?", "tobacco": "Табак?"}
KINDS = ["да", "нет", "противоречие", "автомат", "нет данных"]
# Source groups in the order of `existence.groups` in config/config.yaml.
GROUPS = ["OSM", "2GIS", "Яндекс", "Overture", "Foursquare", "Администрация", "ЕГАИС"]
# Groups a category does not have at all (not "0 found": not a source there).
NOT_A_SOURCE = {"alcohol": {"Администрация"}, "tobacco": {"Администрация", "ЕГАИС"}}
LEGEND_SHEET = "Подтверждение"
LEGEND_HEADING = "Как читать"
FRONT = ["Подтверждено", "Основания", "Объект №", "Объект (склеен)", "Подтверждено (объект)"]
KIND_REASONS = {"catering": "Общепит: основания", "alcohol": "Вид: основания", "tobacco": "Вид: основания"}
# What the supervisor was told before objects were merged (records, «вывеска в точке»);
# the README compares them with the record counts the current summaries carry.
OLD_CATERING = {"Архангельск": (803, 1141), "Северодвинск": (373, 467), "Новодвинск": (50, 80)}
# Confirmed merged objects in the supervisor's build of 2026-11-22, before the Yandex rules of
# 2026-11-24 and the fetch of 2026-11-25 (docs/changes/2026-11-24-finalest-yandex-rules.md).
BUILD_2211 = {
    ("Архангельск", "общепит"): 705, ("Северодвинск", "общепит"): 325, ("Новодвинск", "общепит"): 45,
    ("Архангельск", "алкоголь"): 725, ("Северодвинск", "алкоголь"): 346, ("Новодвинск", "алкоголь"): 73,
    ("Архангельск", "табак"): 474, ("Северодвинск", "табак"): 205, ("Новодвинск", "табак"): 51,
}


# --------------------------------------------------------------------- parsing

@dataclass
class Summary:
    """One delivery's summary.txt: `label: value` lines and the combinations table."""

    title: str
    values: dict[str, str]
    combos: dict[str, int] = field(default_factory=dict)


def parse_summary(text: str) -> Summary:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("== "):
        raise ValueError("summary does not start with '== <file>'")
    values: dict[str, str] = {}
    combos: dict[str, int] = {}
    in_combos = False
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith("Источники объекта (комбинации):"):
            in_combos = True
            continue
        if in_combos:
            if line.strip() == "sources":
                continue
            m = re.fullmatch(r"(.*?)\s+(\d+)", line.strip())
            if not m:
                raise ValueError(f"bad combination line: {line!r}")
            combos[m.group(1)] = int(m.group(2))
            continue
        label, sep, value = line.rpartition(": ")  # labels contain ": ", values never do
        if not sep:
            raise ValueError(f"bad summary line: {line!r}")
        values[label.strip()] = value.strip()
    return Summary(lines[0][3:].strip(), values, combos)


def pair(value: str) -> tuple[int, int]:
    """'1043 (записей 1141)' -> (1043, 1141): merged objects, records."""
    m = re.fullmatch(r"(\d+) \(записей (\d+)\)", value)
    if not m:
        raise ValueError(f"not 'N (записей M)': {value!r}")
    return int(m.group(1)), int(m.group(2))


def pairs(value: str) -> list[tuple[int, int]]:
    """'143 / 110 (записей 186 / 119)' -> [(143, 186), (110, 119)]."""
    m = re.fullmatch(r"([\d / ]+) \(записей ([\d / ]+)\)", value)
    if not m:
        raise ValueError(f"not 'A / B (записей C / D)': {value!r}")
    objs = [int(x) for x in m.group(1).split(" / ")]
    recs = [int(x) for x in m.group(2).split(" / ")]
    return list(zip(objs, recs, strict=True))


def confirmed(value: str) -> dict[str, int | str]:
    """'705 из 1043 (67.6%); записей 803 из 1141[; ...]' -> its four counts and the percent as written."""
    m = re.match(r"(\d+) из (\d+) \(([\d.]+)%\); записей (\d+) из (\d+)", value)
    if not m:
        raise ValueError(f"not a confirmed-objects value: {value!r}")
    return {"objects": int(m.group(1)), "of": int(m.group(2)), "pct": m.group(3),
            "records": int(m.group(4)), "records_of": int(m.group(5))}


def source_counts(combos: dict[str, int]) -> dict[str, int]:
    """Merged objects per counted source group, read off the combinations table.

    The table lists each merged object once under the set of groups its pooled
    records counted, so a group's objects are the sum over the sets holding it.
    These are separate columns, never added to one another.
    """
    out = dict.fromkeys(GROUPS, 0)
    for combo, n in combos.items():
        for g in combo.split("; "):
            if g == "—":
                continue
            if g not in out:
                raise ValueError(f"unknown source group {g!r}")
            out[g] += n
    return out


# --------------------------------------------------------------------- one delivery

@dataclass
class Delivery:
    delivery: str
    category: str
    city: str
    workbook: Path
    summary_path: Path
    summary: Summary

    @property
    def name(self) -> str:
        return f"{self.city}_{CATEGORY[self.category]}.xlsx"


def find(deliveries: pd.DataFrame) -> list[Delivery]:
    out = []
    for r in deliveries.itertuples(index=False):
        d = FINALEST / r.delivery
        books = sorted(d.glob("*_подтверждено.xlsx"))
        if len(books) != 1:
            raise FileNotFoundError(f"{d}: expected one *_подтверждено.xlsx, found {len(books)}")
        sp = d / "summary.txt"
        s = parse_summary(sp.read_text(encoding="utf-8"))
        if s.title != Path(r.path).name:
            raise RuntimeError(f"{sp}: summary is of {s.title!r}, the delivery is {Path(r.path).name!r}")
        out.append(Delivery(r.delivery, r.category, r.city, books[0], sp, s))
    out.sort(key=lambda x: (list(CATEGORY).index(x.category), CITY_ORDER.index(x.city)))
    return out


def check_workbook(d: Delivery) -> list[str]:
    """Problems with the workbook's legend and front columns ([] if none). Read only."""
    import openpyxl

    wb = openpyxl.load_workbook(d.workbook, read_only=True)
    try:
        problems = []
        if LEGEND_SHEET not in wb.sheetnames:
            return [f"нет листа-легенды «{LEGEND_SHEET}» (листы: {', '.join(wb.sheetnames)})"]
        legend = [[c for c in r if c is not None] for r in wb[LEGEND_SHEET].iter_rows(values_only=True)]
        if not legend or not legend[0] or legend[0][0] != LEGEND_HEADING:
            problems.append(f"лист «{LEGEND_SHEET}» не начинается с «{LEGEND_HEADING}»")
        text = " ".join(str(c) for r in legend for c in r)
        header = next(wb["data"].iter_rows(max_row=1, values_only=True))
        for col in [*FRONT, KIND_LABEL[d.category], KIND_REASONS[d.category]]:
            if col not in header:
                problems.append(f"на листе data нет колонки «{col}»")
        for col in ["Подтверждено", "Основания", "Подтверждено (объект)", "Объект (склеен)",
                    KIND_LABEL[d.category]]:
            if col not in text:
                problems.append(f"легенда не объясняет «{col}»")
        return problems
    finally:
        wb.close()


def row(d: Delivery) -> dict[str, object]:
    """The summary-table row of one delivery, every number from its summary.txt."""
    v = d.summary.values
    n_obj, n_rec = pair(v.get("Объектов") or v["Объектов (уникальных вывесок в точке)"])
    c = confirmed(v["Подтверждено объектов"])
    if (c["of"], c["records_of"]) != (n_obj, n_rec):
        raise RuntimeError(f"{d.summary_path}: confirmed denominators {c} disagree with objects {n_obj}/{n_rec}")
    src = source_counts(d.summary.combos)
    combos_total = sum(d.summary.combos.values())
    if combos_total != n_obj:
        raise RuntimeError(f"{d.summary_path}: combinations cover {combos_total} objects, not {n_obj}")
    two = sum(n for k, n in d.summary.combos.items() if k != "—" and len(k.split("; ")) >= 2)
    if two != c["objects"]:
        raise RuntimeError(f"{d.summary_path}: {two} objects with >= 2 groups, but {c['objects']} confirmed")
    for g in NOT_A_SOURCE.get(d.category, ()):
        if src[g]:
            raise RuntimeError(f"{d.summary_path}: {g} counted {src[g]} times, but it is not a source here")
    fsq_counted, fsq_stale = pairs(v["Foursquare: засчитано / найдено, но старое или закрыто (объектов)"])
    if fsq_counted[0] != src["Foursquare"]:
        raise RuntimeError(f"{d.summary_path}: Foursquare {fsq_counted[0]} vs combinations {src['Foursquare']}")

    out: dict[str, object] = {
        "Город": d.city,
        "Категория": CATEGORY[d.category],
        "Строк выгрузки": int(v["Строк"]),
        "Записей": n_rec,
        "Объектов": n_obj,
        "Подтверждено объектов": c["objects"],
        "Подтверждено, %": float(c["pct"]),
        "Подтверждено записей": c["records"],
    }
    for g in GROUPS:
        out[f"Источник: {g}"] = "не источник" if g in NOT_A_SOURCE.get(d.category, ()) else src[g]
    out["Вид проверяется как"] = KIND_LABEL[d.category]
    for k in KINDS:
        out[f"Вид: {k}"] = pair(v[f"{KIND_LABEL[d.category]} — {k} (объектов)"])[0]
    out["Файл"] = f"data/{d.name}"
    return out


def uncounted(d: Delivery) -> dict[str, object]:
    """Evidence found and shown after « || » but not counted -- one column per kind, never summed."""
    v = d.summary.values
    na = "—"
    fsq = pairs(v["Foursquare: засчитано / найдено, но старое или закрыто (объектов)"])[1][0]
    out: dict[str, object] = {
        "Город": d.city,
        "Категория": CATEGORY[d.category],
        "Яндекс: «Больше не работает»": pair(v["closed"])[0],
        "Яндекс: «не опознан» (общая вывеска)": pair(v["ambiguous"])[0],
        "Foursquare: найдено, но старое или закрыто": fsq,
        "«Найдено» подрядчика, отклонено по имени": (
            pair(v["«Найдено» подрядчика, отклонено по имени (объектов)"])[0] if d.category == "catering" else na),
        "ЕГАИС: своя действующая лишь по адресу/общей вывеске": (
            pair(v["ЕГАИС: своя действующая лишь по адресу или общей вывеске (не засчитана)"])[0]
            if d.category == "alcohol" else na),
        "Табак: своя действующая розничная алкогольная лицензия (probable)": (
            pair(v["Своя действующая розничная алкогольная лицензия (probable: косвенно, "
                   "не проверка, не суммировать)"])[0] if d.category == "tobacco" else na),
        "Табак: своя алкогольная лицензия архивная/нерозничная (weak)": (
            pair(v["Своя алкогольная лицензия архивная или нерозничная (weak)"])[0]
            if d.category == "tobacco" else na),
        "Подтверждено, но Яндекс пишет «закрыт»": pair(v["Подтверждено, но Яндекс пишет «закрыт»"])[0],
    }
    return out


# --------------------------------------------------------------------- writing

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "неизвестен"


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def num(x: object) -> str:
    return f"{x:,}".replace(",", " ") if isinstance(x, int) else str(x)


TEXT_COLUMNS = {"Город", "Категория", "Вид проверяется как", "Поставка", "Сводка", "sha256 сводки", "Книга",
                "sha256 книги"}


def md_table(df: pd.DataFrame, columns: list[str], rename: dict[str, str] | None = None) -> str:
    rename = rename or {}
    head = [rename.get(c, c) for c in columns]
    align = ["---" if c in TEXT_COLUMNS else "---:" for c in columns]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(align) + "|"]
    for r in df[columns].itertuples(index=False):
        cells = [pct(x) if c == "Подтверждено, %" else num(x) for c, x in zip(columns, r, strict=True)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


LEGEND_ROWS = [
    ("Строка", "Одна поставка: город × категория. Числа — из summary.txt, записанного конвейером рядом с книгой "
               "(тот же блок, что в results/finalest/<категория>_summary.txt); путь и sha256 — на листе "
               "«Источник чисел»."),
    ("Записей / Объектов", "Запись — «вывеска в точке» (строки выгрузки, повторяющие вывеску под разными типами, "
                           "уже сведены). Объект — склеенный объект: записи, связанные склейкой OSM↔2GIS, общей "
                           "карточкой Overture/Foursquare/ЕГАИС или магазином сверки с лицензиями. Итоги — по "
                           "объектам (methodology.md, §1.2)."),
    ("Подтверждено объектов", "«Подтверждено (объект)» = 1: не меньше двух независимых групп источников дали "
                              "сильную и актуальную находку по объединённым свидетельствам записей объекта. "
                              "Процент — как в сводке конвейера."),
    ("Подтверждено записей", "То же правило по каждой записи отдельно (колонка «Подтверждено»). Ранее выданные "
                             "цифры общепита считались так."),
    ("Источник: …", "Сколько объектов засчитали находку этой группы (сильная, актуальная). Каждая колонка — "
                    "отдельный вид свидетельства; колонки НЕ складываются: у подтверждённого объекта их не "
                    "меньше двух. «не источник» — группа для этой категории не применяется (ЕГАИС для табака, "
                    "реестр администрации вне общепита). «Администрация» = 0 везде: колонка Arkh_admin есть "
                    "во всех девяти выгрузках, но пуста во всех строках, так что голосов эта группа не дала."),
    ("Вид: …", "Колонка книги «Общепит?» / «Алкоголь?» / «Табак?» (см. «Вид проверяется как»). Проверка вида, "
               "отдельно от существования: да / нет / противоречие / автомат (вендинг, только общепит) / нет "
               "данных. Склеенный объект, у записей которого вид разный, учтён под каждым значением, поэтому "
               "сумма может немного превышать число объектов."),
    ("Лист «Не засчитано»", "Находки, которые видны в «Основаниях» после « || », но в «Подтверждено» не входят: "
                            "закрытые и неопознанные карточки Яндекса, устаревшие записи Foursquare, лицензии не "
                            "своей вывески. Для табака алкогольная лицензия — косвенный довод (probable / weak), "
                            "никогда не подтверждение. Эти числа ни с чем не суммируются."),
]


def write_xlsx(path: Path, table: pd.DataFrame, weak: pd.DataFrame, provenance: pd.DataFrame) -> None:
    from openpyxl.styles import Alignment, Font

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        table.to_excel(xw, sheet_name="Сводка", index=False)
        weak.to_excel(xw, sheet_name="Не засчитано", index=False)
        pd.DataFrame(LEGEND_ROWS, columns=["Колонка", "Что значит"]).to_excel(xw, sheet_name="Как читать",
                                                                               index=False)
        provenance.to_excel(xw, sheet_name="Источник чисел", index=False)
        for name, ws in xw.sheets.items():
            ws.freeze_panes = "C2" if name in ("Сводка", "Не засчитано") else "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 18 if name in (
                    "Сводка", "Не засчитано") else 110)
            if name == "Как читать":
                for r in ws.iter_rows(min_row=2):
                    for c in r:
                        c.alignment = Alignment(wrap_text=True, vertical="top")


def summary_md(table: pd.DataFrame, weak: pd.DataFrame, provenance: pd.DataFrame, head: str) -> str:
    kinds = [f"Вид: {k}" for k in KINDS]
    src = [c for c in table.columns if c.startswith("Источник: ")]
    parts = [
        "# Сводка проверки финальной поставки",
        "",
        "Строки — поставки (город × категория). Все числа прочитаны из `summary.txt`, которые конвейер записал "
        "рядом с каждой книгой (`results/finalest/finalest_<город>_<категория>/summary.txt`, тот же блок, что в "
        f"`results/finalest/<категория>_summary.txt`). Сборка: `pixi run deliverable`, коммит `{head}`. "
        "Пути и sha256 — в конце файла.",
        "",
        "**Разные виды свидетельств не суммируются.** Колонки источников — отдельные показатели; подтверждённый "
        "объект учтён в двух и более из них. Существование и вид — разные вопросы.",
        "",
        "## 1. Объём и подтверждение существования",
        "",
        "Объект — склеенный объект, запись — «вывеска в точке». Подтверждено = не меньше двух независимых групп "
        "источников с сильной актуальной находкой.",
        "",
        md_table(table, ["Город", "Категория", "Строк выгрузки", "Записей", "Объектов", "Подтверждено объектов",
                         "Подтверждено, %", "Подтверждено записей"]),
        "",
        "## 2. Засчитанные находки по группам источников (объектов)",
        "",
        "Сколько объектов засчитали находку каждой группы. Колонки не складываются. «не источник» — группа в этой "
        "категории не применяется: ЕГАИС для табака (в реестре нет табачного класса), реестр администрации — "
        "только общепит. «Администрация» = 0 в общепите всех трёх городов не потому, что флаг не читается: "
        "колонка `Arkh_admin` есть во всех девяти выгрузках, но пуста во всех строках. Голосов эта группа не "
        "дала. OSM и 2GIS засчитаны по флагам самой выгрузки, нами не перепроверены.",
        "",
        md_table(table, ["Город", "Категория", *src], {c: c.removeprefix("Источник: ") for c in src}),
        "",
        "## 3. Проверка вида (объектов)",
        "",
        "Отдельно от существования. Склеенный объект с разным видом у записей учтён под каждым значением, "
        "поэтому сумма строки может немного превышать число объектов. «автомат» бывает только в общепите.",
        "",
        md_table(table, ["Город", "Категория", "Вид проверяется как", "Объектов", *kinds],
                 {c: c.removeprefix("Вид: ") for c in kinds} | {"Вид проверяется как": "Колонка"}),
        "",
        "## 4. Найдено, но не засчитано (объектов)",
        "",
        "Видно в «Основаниях» после « || ». Эти числа не входят в «Подтверждено» и ни с чем не суммируются. "
        "Для табака алкогольная лицензия — косвенный довод, не проверка (probable — своя действующая розничная, "
        "weak — архивная или нерозничная).",
        "",
        md_table(weak, list(weak.columns)),
        "",
        "## Источник чисел",
        "",
        md_table(provenance, list(provenance.columns)),
        "",
    ]
    return "\n".join(parts)


def readme(table: pd.DataFrame, deliveries: list[Delivery], head: str) -> str:
    cat = table[table["Категория"].eq("общепит")].set_index("Город")
    shift = []
    for city, (old_c, old_n) in OLD_CATERING.items():
        r = cat.loc[city]
        if r["Записей"] != old_n:
            raise RuntimeError(f"{city}: {r['Записей']} catering records, not the {old_n} given earlier")
        shift.append(f"| {city} | {num(old_c)} из {num(old_n)} | {num(int(r['Подтверждено записей']))} из "
                     f"{num(int(r['Записей']))} | {num(int(r['Подтверждено объектов']))} из "
                     f"{num(int(r['Объектов']))} ({pct(r['Подтверждено, %'])}) |")
    since = []
    for _, r in table.iterrows():
        was, now = BUILD_2211[(r["Город"], r["Категория"])], int(r["Подтверждено объектов"])
        since.append(f"| {r['Город']} | {r['Категория']} | {num(was)} | {num(now)} | {now - was:+d} |")
    files = "\n".join(f"  - `data/{d.name}` — копия `{rel(d.workbook)}`" for d in deliveries)
    return f"""# Проверка финальной поставки: материалы для руководителя

Собрано `pixi run deliverable` (`workflow/scripts/build_deliverable.py`), коммит `{head}`.
Ничего не пересчитано: книги скопированы из результатов конвейера, числа сводки прочитаны из его сводок.

## Что в папке

- `data/` — девять итоговых книг, по одной на город × категорию. Лист `data` — выгрузка подрядчика,
  спереди добавлены «Подтверждено», «Основания», вид («Общепит?» / «Алкоголь?» / «Табак?»), в конце —
  «Объект №», «Объект (склеен)», «Подтверждено (объект)», основания вида и столбцы свидетельств.
  Лист «Подтверждение» начинается с раздела «Как читать» (легенда каждой колонки), ниже — «Итоги» файла.
  Колонки подрядчика оставлены как сданы.
{files}
- `сводка.xlsx`, `сводка.md` — сводная таблица по девяти поставкам: объём, подтверждение, засчитанные
  источники по группам, проверка вида, найденное, но не засчитанное; лист «Как читать» и источник каждого числа.
- `methodology.md` — методология проверки и валидации (копия `docs/methodology_finalest.md`).

## Как читать «Основания»

До знака « || » — что засчитано, по группам источников, со ссылкой: запись OSM или карточка 2GIS,
карточка Яндекса (с датой нашей проверки), соседняя запись, давшая провайдера, место Overture/Foursquare,
лицензия ЕГАИС. После « || » — что искали и не нашли или нашли, но не засчитали, и почему: «закрыт»,
«устарело», «не своя лицензия», «только адрес». Любой 0 или 1 проверяется по этим ссылкам без
повторного запуска.

## Что значат вердикты

- **Подтверждено = 1** — о существовании говорят не меньше двух независимых групп источников
  (OSM, 2GIS, Яндекс, Overture, Foursquare, ЕГАИС — кроме табака; одна группа — один голос).
  Группа «реестр администрации» (флаг `Arkh_admin`) предусмотрена для общепита, но в выгрузках
  finalest эта колонка пуста во всех строках всех девяти файлов, поэтому голосов она не дала.
  OSM и 2GIS засчитываются по флагам самой выгрузки подрядчика, нами не перепроверены: часть
  подтверждённых объектов держится только на них (methodology.md, §2).
  Это не значит, что объект работает сегодня и что он того вида.
- **Подтверждено = 0** — очередь для ручной проверки, не приговор: у неподтверждённых объектов
  засчитан ровно один источник, как правило собственный провайдер выгрузки.
- **Подтверждено (объект)** — то же правило по объединённым свидетельствам всех записей склеенного
  объекта («Объект (склеен)»). Итоги считаются по нему.
- **Вид**: да — все высказавшиеся источники за; нет — все против; противоречие — расходятся;
  автомат — вендинг (только общепит); нет данных — описаний вида нет. Вид и существование не
  объединяются в один показатель.
- **Табак**: в реестре ЕГАИС нет табачного класса. Алкогольная лицензия у табачной точки — косвенный
  довод после « || », никогда не подтверждение и не суммируется с прямыми свидетельствами.

## Цифры общепита сдвинулись

Ранее сообщались числа по **записям** («вывеска в точке»). Теперь итог считается по **склеенным
объектам**: дубли одной вывески (OSM и 2GIS рядом, общая карточка источника) больше не считаются двумя
объектами. Старые и новые проценты напрямую не сравниваются. Число подтверждённых записей тоже
сдвинулось, по причинам из следующего раздела.

| Город | Было (записи) | Сейчас (записи) | Сейчас (объекты) |
|---|---:|---:|---:|
{chr(10).join(shift)}

## Что изменилось после сборки 22.11.2026

1. **Новые правила для Яндекса** (24.11). Если наш запрос читает ту же карточку, которую засчитал
   подрядчик, и на ней «Больше не работает», отметка подрядчика больше не засчитывается. Одна карточка
   подтверждает одну точку (ближайший объект и объекты в 50 м от него). Для адреса без номера дома
   делается второй запрос «название, город».
2. **Наш запрос к Яндексу для каждого именованного объекта** (загрузка 25.11), а не только там, где
   от него зависел вердикт.

Каждый сдвиг разобран по объектам; список — `docs/changes/2026-11-24-finalest-yandex-rules.md`
в репозитории. Подтверждённые объекты:

| Город | Категория | 22.11 | Сейчас | Разница |
|---|---|---:|---:|---:|
{chr(10).join(since)}

Метрики валидации ниже и в `methodology.md` (§8, §9) посчитаны на конфигурации 22.11 и
с этими правилами не пересчитывались.

## Метрики валидации

Доля «Подтверждено» описывает работу правила, а не его точность. Итоговые метрики на выборке B
(435 объектов, по 145 на категорию) — в разделе 8 `methodology.md`. Коротко:

- эталон — аудитор-языковая модель по строгому правилу силы метки (§5.3). Он разрешил существование
  только у 88 из 435 объектов (20 %): для большинства объектов открытый веб не даёт независимого
  датированного свидетельства. Неясных больше 20 % во всех категориях, поэтому результат — границы:
  точность «Подтверждено» 0,21 – 0,97 (алкоголь), 0,26 – 0,93 (общепит), 0,12 – 0,95 (табак);
- вторично, по разрешённым меткам: точность 0,84 / 0,77 / 0,74 с интервалами шириной 38–56 п. п. Это
  объекты с заметным свежим следом в сети, а не вся поставка;
- полнота без ручной проверки не идентифицируема (границы 0,39 – 1,00, 0,40 – 0,97, 0,29 – 1,00);
- точность вида «да» в общепите 0,96 [0,82; 1,00] на 12 опознанных объектах; для алкоголя и табака
  данных недостаточно;
- ручная проверка аудитора человеком не выполнена, поэтому все числа условны относительно аудитора.
  Числа калибровочной выборки A (§6) — разведочные, качеством поставки не являются;
- эскалации к более сильной модели на B не было: 361 неясный объект переспрошен той же `gpt-6-luna`
  по инструкции версии 3 (столбец `escalated` отмечает переспрос, а не смену модели; §8.1);
- правило объединения страт для дисперсии расширено после вскрытия B (оценщик падал на стратах с одной
  разрешённой меткой). Оно меняет только ДИ, не точечные оценки и не границы неясности (§8.7).
"""


# Rows of the README's internal-validity table: source key of metrics.csv -> label.
IV_ROWS = {
    "yandex": "Яндекс (наша проверка)",
    "yandex_contractor": "Яндекс подрядчика",
    "relink": "Соседняя запись выгрузки (relink)",
    "fsq": "Foursquare",
    "overture": "Overture",
    "egais": "ЕГАИС",
    "own": "**Все решения конвейера, без склейки** (взвешено по популяции решений)",
    "merge": "Склейка подрядчика OSM↔2GIS (принята на веру)",
}


def iv_pct(x: float) -> str:
    return f"{100 * x:.1f}".replace(".", ",")


def iv_cell(m: pd.DataFrame, source: str, metric: str) -> str:
    """'a % [lo; hi] (границы x–y %)', or counts only where fewer than 10 labels are resolved."""
    r = m[(m["reference"] == "consensus") & (m["source"] == source) & (m["metric"] == metric)]
    if r.empty:
        return "—"
    r = r.iloc[0]
    if not bool(r["sufficient"]):
        return f"данных недостаточно ({int(r['bad'])} из {int(r['resolved'])})"
    return (f"{iv_pct(r['point'])} % [{iv_pct(r['lo95'])}; {iv_pct(r['hi95'])}] "
            f"(границы {iv_pct(r['bound_lo'])}–{iv_pct(r['bound_hi'])} %)")


def internal_validity_md(eval_dir: Path = IV_EVAL) -> str:
    """README block from `pixi run internal-validity`; numbers are read, never typed in."""
    path = eval_dir / "metrics.csv"
    if not path.exists():
        raise SystemExit(f"{rel(path)} is missing: run `pixi run internal-validity` first")
    m = pd.read_csv(path, encoding="utf-8-sig")
    ag = pd.read_csv(eval_dir / "agreement.csv", encoding="utf-8-sig")
    kappa = ag[(ag["source"] == "all") & (ag["question"] == "same_org")]["kappa"].iloc[0]
    kappa = f"{kappa:.2f}".replace(".", ",")
    rows = "\n".join(f"| {label} | {iv_cell(m, src, 'false_accept')} | {iv_cell(m, src, 'false_reject')} |"
                     for src, label in IV_ROWS.items())
    own_fa, own_fr = iv_cell(m, "own", "false_accept"), iv_cell(m, "own", "false_reject")
    merge_fa = iv_cell(m, "merge", "false_accept")
    head = "| Источник | Ложные принятия: P(не тот \\| принят) | Ложные отказы: P(тот \\| отвергнут) |"
    return f"""
## Внутренняя корректность vs внешняя достоверность

Это разные вопросы. **Внутренняя корректность** — верно ли конвейер сопоставляет объект с записями
*своих* источников (кэш Яндекса, снимки Overture и Foursquare, выписка ЕГАИС, лист `quality`
подрядчика). **Внешняя достоверность** — существует ли объект на самом деле (§8, раздел выше). Первое
не доказывает второго: верно сопоставленная устаревшая карточка остаётся устаревшей.

Оценка: 260 решений «объект ↔ запись» в 22 стратах, два слепых судьи-LLM разных моделей (Sonnet,
Opus), только по данным пакета; эталон — совпавшие ответы, остальное входит в границы. Веса — популяция
решений страты / выборка. 95 % ДИ — Уилсон по эффективному объёму. Разведочно, без поправки на
множественность (methodology.md, §9).

{head}
|---|---|---|
{rows}

**Вывод.** При доступе к источникам Яндекс, Foursquare, Overture, ЕГАИС, соседние записи выгрузки и
Яндекс подрядчика конвейер ошибочно принимает совпадение в {own_fa} случаев и ошибочно отвергает
лучшего кандидата в {own_fr} случаев. Обе доли взвешены по популяции всех решений этих источников;
по источникам — таблица. Склейка OSM↔2GIS, которую конвейер берёт у подрядчика без проверки, содержит
запись другой организации в {merge_fa} склеек. Это не обязательно ложное подтверждение: какая запись
от какого провайдера, в выгрузке не указано.

Оговорки: судьи — языковые модели, не человек (согласие κ = {kappa}); ложные отказы оценены на
лучшем отвергнутом кандидате в радиусе проверки, отказы по расстоянию в оценку не входят; часть
решений ЕГАИС требует знать, какое юрлицо держит вывеску, и осталась неясной.
"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    deliveries = find(pd.read_csv(DELIVERIES, sep="\t", dtype=str))
    problems = {d.name: check_workbook(d) for d in deliveries}
    bad = {k: v for k, v in problems.items() if v}
    if bad:
        raise SystemExit("Книги не готовы к выдаче:\n" + "\n".join(f"  {k}: {'; '.join(v)}" for k, v in bad.items()))

    table = pd.DataFrame([row(d) for d in deliveries])
    weak = pd.DataFrame([uncounted(d) for d in deliveries])
    head = git_head()

    out: Path = args.out
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)
    for d in deliveries:
        shutil.copy2(d.workbook, out / "data" / d.name)
    provenance = pd.DataFrame([{"Поставка": f"{d.city} / {CATEGORY[d.category]}", "Сводка": rel(d.summary_path),
                                "sha256 сводки": sha256(d.summary_path)[:16], "Книга": f"data/{d.name}",
                                "sha256 книги": sha256(out / "data" / d.name)[:16]} for d in deliveries])
    if any(sha256(d.workbook) != sha256(out / "data" / d.name) for d in deliveries):
        raise RuntimeError("a copied workbook differs from its source")

    write_xlsx(out / "сводка.xlsx", table, weak, provenance)
    (out / "сводка.md").write_text(summary_md(table, weak, provenance, head), encoding="utf-8")
    shutil.copy2(METHODOLOGY, out / "methodology.md")
    text = readme(table, deliveries, head) + internal_validity_md()
    if MERGE_REVIEW.exists():
        # Contractor OSM+2GIS merges whose names disagree: a review list, never a filter.
        merge = pd.read_csv(MERGE_REVIEW, encoding="utf-8-sig")
        merge = merge[merge["low"]].sort_values(["only_osm_gis", "cat", "city"], ascending=[False, True, True])
        merge.to_excel(out / "к_проверке_склейки_OSM_2GIS.xlsx", index=False)
        text += (
            "\n## Список к проверке: склейки OSM + 2GIS с несхожими именами\n\n"
            f"`к_проверке_склейки_OSM_2GIS.xlsx` — {len(merge)} подтверждённых объектов, у которых подрядчик "
            "склеил запись OSM и запись 2GIS с несхожими именами (сходство < 72). "
            f"{int(merge['only_osm_gis'].sum())} из них подтверждены только этими двумя флагами "
            "(`only_osm_gis = True`, они в начале списка) — такие подтверждения могут быть ложными "
            "(два заведения в одном доме). Низкое сходство не доказывает ошибку (оператор и вывеска "
            "одной сети), поэтому это список для проверки, а не исключение. Подробно — methodology.md §7, п. 9.\n"
        )
    (out / "README.md").write_text(text, encoding="utf-8")
    print(f"{out}: {len(deliveries)} книг, сводка, methodology.md, README.md")


if __name__ == "__main__":
    main()
