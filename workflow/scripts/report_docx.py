"""The supervisor's short report: two Word documents with diagrams and the cover message.

Called by `build_deliverable.py` once the base is built, so the report always
describes the folder it lies in. Nothing is typed in: every number is read
from the unified base (`база/единая_база.xlsx`), the deliverable's summary
tables, the sample-B evaluation of the current verdicts and of the base
(`calibration_current.py`) and the internal-validity evaluation
(`internal_validity_eval.py`). Diagrams are Graphviz, rendered with `dot`.

Writes into `<deliverable>/Методика/`:
- «Методика проверки — общепит, алкоголь, табак.docx»: how the base is formed;
- «Оценка качества проверки — общепит, алкоголь, табак.docx»: how right it is;
and `<deliverable>/сообщение_руководителю.txt`.

Reports are Russian (CLAUDE.md); the base and these files carry 2GIS and
Yandex data and are not for publication.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT / "results" / "finalest" / "calibration"
IV = ROOT / "results" / "finalest" / "internal_validity_v2"
FOLDER = "Методика"
METHOD_DOC = "Методика проверки — общепит, алкоголь, табак.docx"
QUALITY_DOC = "Оценка качества проверки — общепит, алкоголь, табак.docx"
MESSAGE = "сообщение_руководителю.txt"

CATS = [("catering", "Общепит"), ("alcohol", "Алкоголь"), ("tobacco", "Табак")]
KIND_COL = {"Общепит": "Общепит?", "Алкоголь": "Алкоголь?", "Табак": "Табак?"}
SOURCE_FLAGS = [
    ("OSM", "OSM"),
    ("GIS", "2GIS"),
    ("yandex", "Яндекс"),
    ("overture", "Overture"),
    ("fsq", "Foursquare"),
    ("egais", "ЕГАИС"),
]
MANUAL = {
    "не в базе: один источник, в Яндексе в 60 м карточка с другим названием": "Не в базе: один источник, а в Яндексе в 60 м карточка с другим названием "
    "(переименование, транслитерация или другая организация на месте)",
    "не в базе: в Яндексе пометка «Больше не работает», закрыт ли объект": "Не в базе: в Яндексе «Больше не работает» — закрыт ли объект",
    "в базе, но источники расходятся, относится ли объект к виду": "В базе, но источники расходятся, того ли вида объект",
}


# --------------------------------------------------------------------- formatting


def n(x) -> str:
    """1 062 with a no-break space, as in the summary."""
    return f"{int(x):,}".replace(",", " ")


def dec(x: float, k: int = 2) -> str:
    return f"{x:.{k}f}".replace(".", ",")


def pc(x: float, k: int = 1) -> str:
    return f"{100 * x:.{k}f}".replace(".", ",") + " %"


def plural(k: int, one: str, few: str, many: str) -> str:
    """Russian noun form after a number: 1 объект, 2 объекта, 5 объектов (11–14: many)."""
    k = abs(int(k))
    if k % 10 == 1 and k % 100 != 11:
        return one
    if 2 <= k % 10 <= 4 and not 12 <= k % 100 <= 14:
        return few
    return many


def objs(k: int, adj: str = "") -> str:
    """«672 подтверждённых объекта»: the number, an optional adjective, the noun in its form."""
    noun = plural(k, "объект", "объекта", "объектов")
    if adj:
        adj = adj if plural(k, "1", "2", "5") != "1" else adj.replace("ых", "ый")
    return f"{n(k)} {adj + ' ' if adj else ''}{noun}"


def model(label: str) -> str:
    """«Судья 1 (gpt-6.2-sol)» -> «gpt-6.2-sol»."""
    return re.search(r"\(([^),]+)", label).group(1)


def rng(lo: float, hi: float, k: int = 2) -> str:
    return f"{dec(lo, k)} – {dec(hi, k)}"


# --------------------------------------------------------------------- numbers


@dataclass
class Numbers:
    stages: pd.DataFrame  # «Этапы» of the base, three categories + totals
    base: pd.DataFrame  # «База», one row per merged object
    table: pd.DataFrame  # the deliverable's summary table (per delivery)
    weak: pd.DataFrame  # found but not counted (per delivery)
    build_2211: int  # confirmed objects of the 22.11 build
    merge_review: pd.DataFrame
    ext_base: pd.DataFrame  # sample-B metrics, verdict = «В базу»
    ext_cur: pd.DataFrame  # sample-B metrics, verdict = «Подтверждено (объект)»
    ext_counts: dict  # unweighted counts behind the base's existence share
    labels_b: dict  # how many B labels were resolved
    iv: pd.DataFrame
    iv_agree: pd.DataFrame
    iv_strata: pd.DataFrame
    iv_manifest: dict
    iv_judges: dict
    iv_moved: dict  # Yandex false rejections: where they land in the base

    # -- base
    def total(self, cat: str) -> pd.Series:
        st = self.stages
        return st[(st["Город"] == "Все три города") & (st["Вид объекта"] == cat)].iloc[0]

    def tot(self, col: str) -> int:
        st = self.stages
        return int(st.loc[st["Город"] == "Все три города", col].sum())

    def cat_rows(self, cat: str) -> pd.DataFrame:
        return self.base[self.base["Вид объекта"] == cat]

    def only_osm_gis(self, cat: str | None = None) -> int:
        b = self.base if cat is None else self.cat_rows(cat)
        return int(((b["Подтверждено"] == 1) & (b["Источников"] == 2) & (b["OSM"] == 1) & (b["GIS"] == 1)).sum())

    def manual(self, cat: str) -> dict[str, int]:
        return self.cat_rows(cat)["Что проверить вручную"].fillna("").value_counts().to_dict()

    # -- external validity
    def ext(self, which: str, cat: str, metric: str, analysis: str = "main") -> pd.Series:
        m = self.ext_base if which == "base" else self.ext_cur
        r = m[(m["category"] == cat) & (m["metric"] == metric) & (m["analysis"] == analysis)]
        return r.iloc[0]

    # -- internal validity
    def ivm(self, source: str, metric: str, ref: str = "consensus") -> pd.Series:
        m = self.iv
        return m[(m["reference"] == ref) & (m["source"] == source) & (m["metric"] == metric)].iloc[0]


def ext_cell(r: pd.Series) -> str:
    return f"{dec(r['estimate'])} [{dec(r['low'])}; {dec(r['high'])}]"


def iv_cell(r: pd.Series, bounds: bool = False) -> str:
    if not r["reportable"]:
        return f"данных недостаточно ({int(r['errors'])} из {int(r['resolved'])})"
    s = f"{pc(r['estimate'])} [{dec(100 * r['low'], 1)}; {dec(100 * r['high'], 1)}]"
    return s + (f"; границы {dec(100 * r['bound_low'], 1)}–{dec(100 * r['bound_high'], 1)} %" if bounds else "")


def collect(
    out: Path, base_dir: Path, table: pd.DataFrame, weak: pd.DataFrame, build_2211: int, merge_review: pd.DataFrame
) -> Numbers:
    book = base_dir / "единая_база.xlsx"
    stages = pd.read_excel(book, sheet_name="Этапы")
    base = pd.read_excel(book, sheet_name="База")
    ext_base = pd.read_csv(CAL / "base" / "metrics.csv")
    ext_cur = pd.read_csv(CAL / "current" / "metrics.csv")
    # Unweighted counts behind «share of base objects that exist».
    flags = pd.read_csv(CAL / "base" / "label_flags.csv")
    sample = pd.read_csv(CAL / "base" / "sample_B.csv")
    j = flags.merge(sample[["unit_id", "ver_exists"]].rename(columns={"ver_exists": "v"}), on="unit_id")
    counts = {}
    for cat, _ in CATS:
        part = j[(j["category"] == cat) & (j["v"] == "conf")]
        counts[cat] = {
            "in_base": len(part),
            "resolved": int(part["E"].notna().sum()),
            "exist": int(part["E"].eq(1).sum()),
        }
    labels_b = {"n": len(flags), "resolved": int(flags["E"].notna().sum())}
    iv_eval = IV / "eval"
    iv = pd.read_csv(iv_eval / "metrics.csv", encoding="utf-8-sig").rename(
        columns={
            "point": "estimate",
            "lo95": "low",
            "hi95": "high",
            "bound_lo": "bound_low",
            "bound_hi": "bound_high",
            "sufficient": "reportable",
            "bad": "errors",
        }
    )
    labels = pd.read_csv(iv_eval / "labels.csv", encoding="utf-8-sig")
    # Where the Yandex false rejections (both judges: same organisation) land in the base.
    b = base.set_index("ID")
    fr = labels[
        (labels["source"] == "yandex") & (labels["role"] == "rejected") & (labels["err_false_reject_consensus"] == 1)
    ]
    keys = [f"{r.delivery}:{int(r.merged)}" for r in fr.itertuples()]
    rows = b.loc[keys]
    moved = {
        "errors": len(fr),
        "one_source": int((rows["Источников"] < 2).sum()),
        "one_source_manual": int(((rows["Источников"] < 2) & rows["Что проверить вручную"].notna()).sum()),
    }
    return Numbers(
        stages=stages,
        base=base,
        table=table,
        weak=weak,
        build_2211=build_2211,
        merge_review=merge_review,
        ext_base=ext_base,
        ext_cur=ext_cur,
        ext_counts=counts,
        labels_b=labels_b,
        iv=iv,
        iv_agree=pd.read_csv(iv_eval / "agreement.csv", encoding="utf-8-sig"),
        iv_strata=pd.read_csv(iv_eval / "strata.csv", encoding="utf-8-sig"),
        iv_manifest=json.loads((IV / "manifest.json").read_text(encoding="utf-8")),
        iv_judges=json.loads((IV / "judges.json").read_text(encoding="utf-8")),
        iv_moved=moved,
    )


# --------------------------------------------------------------------- diagrams

STYLE = """
  graph [fontname="Liberation Sans", nodesep=0.3, ranksep=0.32, pad=0.2, dpi=200];
  node  [shape=box, style="rounded,filled", fillcolor="#F2F4F7", color="#5B6B7F", fontname="Liberation Sans",
         fontsize=12, margin="0.16,0.07"];
  edge  [color="#5B6B7F", fontname="Liberation Sans", fontsize=10.5, arrowsize=0.7];
"""
Q = 'fillcolor="#FFF4DB", color="#B58A2E"'  # a question / rule
OK = 'fillcolor="#DDF0E2", color="#3D7F55"'  # counted
NO = 'fillcolor="#F7E1E1", color="#A0504F"'  # not counted
IN = 'fillcolor="#E3ECF7"'  # input / output
DIAMOND = f'shape=diamond, style="filled", {Q}, margin="0.04,0.04"'


def dot_pipeline(x: Numbers) -> str:
    objs_ = objs
    rows, nobj, conf = x.tot("Строк в выгрузке"), x.tot("Объектов после склейки"), x.tot("Подтверждено (2+ источника)")
    recs = int(x.table["Записей"].sum())
    manual = int(x.base["Что проверить вручную"].notna().sum())
    return f"""digraph G {{ graph [rankdir=TB]; {STYLE}
  rows [label="Выгрузка подрядчика\\n9 файлов: 3 города × общепит, алкоголь, табак\\n{n(rows)} строк", {IN}];
  recs [label="Запись «вывеска в точке»\\nодна вывеска под разными типами = одна запись\\n{n(recs)} записей"];
  objs [label="Склеенный объект\\nдубли OSM/2GIS в 50 м с тем же именем;\\nобщая карточка Foursquare, Overture, лицензия ЕГАИС\\n{objs_(nobj)}"];
  subgraph cluster_src {{ label="Каждый источник — отдельная группа независимости, не больше одного голоса";
    fontname="Liberation Sans"; fontsize=11; color="#B8C2CF"; style="rounded,dashed";
    osm [label="OSM\\n(флаг выгрузки)"]; gis [label="2GIS\\n(флаг выгрузки)"];
    ya [label="Яндекс Карты\\nнаш запрос +\\n«Найдено» подрядчика"]; ov [label="Overture\\nPlaces"];
    fsq [label="Foursquare\\nOS Places"]; eg [label="Лицензии ЕГАИС\\n(не для табака)"]; }}
  match [label="Находка засчитывается, только если\\nсильная (именно этот объект: имя + расстояние)\\nи актуальная (не закрыта, не устарела, лицензия действует)", {Q}];
  rule [label="Не меньше двух групп?", {DIAMOND}];
  yes [label="Подтверждено = 1\\n{objs_(conf)}", {OK}];
  no [label="Подтверждено = 0\\n{objs_(nobj - conf)}: очередь\\nна проверку, не приговор", {NO}];
  kind [label="Проверка вида — отдельно\\n(название, рубрика Яндекса, категории\\nFoursquare/Overture, лицензия)\\nда / нет / противоречие / нет данных"];
  base [label="В базу: подтверждено, вид не «нет»,\\nв Яндексе не «Больше не работает»\\n{objs_(x.tot("В базу"))}", {OK}, penwidth=1.6];
  man [label="Лист «На ручную проверку»\\n{objs_(manual, "спорных")}\\nсо ссылкой и колонкой решения", {IN}];
  rows -> recs -> objs; objs -> {{osm gis ya ov fsq eg}}; {{osm gis ya ov fsq eg}} -> match; match -> rule;
  rule -> yes [label=" да"]; rule -> no [label=" нет"];
  objs -> kind [style=dashed, constraint=false]; yes -> base; kind -> base [style=dashed];
  no -> man [style=dashed, label=" рядом карточка\\n с другим именем"];
  yes -> man [style=dashed, label=" Яндекс:\\n закрыт"];
  base -> man [style=dashed, label=" вид спорный"];
}}"""


def dot_yandex() -> str:
    return f"""digraph G {{ graph [rankdir=TB]; {STYLE}
  q [label="Запрос «название, город, адрес»\\nс картой по координате объекта\\n(ответ сохраняется в кэш с датой)", {IN}];
  f1 [label="Организация с похожим именем\\n(сходство ≥ 72) в 150 м?\\n(60 м для общей вывески)", {DIAMOND}];
  vag [label="В адресе нет номера дома?", {DIAMOND}];
  q2 [label="Второй запрос «название, город»\\nи та же проверка имени,\\nрасстояния и закрытия"];
  none [label="«нет» или «не опознан»\\n(по адресу кто-то есть,\\nно по общей вывеске не понять кто)", {NO}];
  cl [label="«Больше не работает»?", {DIAMOND}];
  closed [label="Не засчитано, пишется после « || ».\\nЕсли это та же карточка, что засчитал\\nподрядчик, его «Найдено» тоже снимается", {NO}];
  ex [label="Одна карточка — одна точка:\\nкарточка достаётся ближайшему объекту\\nи его дублям в 50 м"];
  ok [label="Засчитано: один голос группы «Яндекс»\\n(вместе с «Найдено» подрядчика,\\nесли у него совпало имя)", {OK}];
  q -> f1; f1 -> cl [label=" да"]; f1 -> vag [label=" нет"]; vag -> q2 [label=" да"]; vag -> none [label=" нет"];
  cl -> closed [label=" да"]; cl -> ex [label=" нет"]; ex -> ok;
}}"""


def dot_egais() -> str:
    return f"""digraph G {{ graph [rankdir=TB]; {STYLE}
  r [label="Реестр лицензий ФСРАР (ЕГАИС), снимок 01.07.2026\\nоставлены только три города", {IN}];
  c [label="Кандидат в 100 м: 0,5·адрес + 0,2·имя + 0,3·расстояние ≥ 0,45\\nи сходство адреса ≥ 0,72 (чтобы не взять соседний дом)"];
  cat [label="Категория поставки", {DIAMOND}];
  tob [label="Табак: табачного класса в реестре нет.\\nСвоя алкогольная лицензия — только косвенный довод\\nпосле « || », никогда не голос", {NO}];
  act [label="Лицензия действует (не архивная)?", {Q}];
  nm [label="Имя совпало?\\nизвестная сеть (Тандер → Магнит), выученная пара\\n«вывеска → владелец», сходство с владельцем или заведением", {Q}];
  weak [label="Не засчитано: архивная, «только адрес»,\\n«общая вывеска»; видно после « || »", {NO}];
  ok [label="Засчитано: голос группы «ЕГАИС»", {OK}];
  r -> c -> cat; cat -> tob [label=" табак"]; cat -> act [label=" общепит, алкоголь"];
  act -> nm [label=" да"]; act -> weak [label=" нет"]; nm -> ok [label=" да"]; nm -> weak [label=" нет"];
}}"""


def dot_quality(x: Numbers) -> str:
    m = x.iv_manifest
    j1, j2 = model(x.iv_judges["judge1"][0]), model(x.iv_judges["judge2"][0])
    kappa = x.iv_agree[(x.iv_agree["source"] == "all") & (x.iv_agree["question"] == "same_org")]["kappa"].iloc[0]
    fa, fr = x.ivm("own", "false_accept"), x.ivm("own", "false_reject")
    mg = x.ivm("merge", "false_accept")
    lb = x.labels_b
    return f"""digraph G {{ graph [rankdir=TB, ranksep=0.3]; {STYLE}
  p [label="Текущие вердикты и база\\n(прогон с правилами Яндекса 24–25.11)", {IN}];
  subgraph cluster_in {{ label="Сопоставление: верно ли решение по своим источникам"; fontname="Liberation Sans"; fontsize=11; color="#B8C2CF"; style="rounded,dashed";
    i1 [label="{m["n_decisions"]} решений «объект ↔ запись»\\nв {len(m["strata"])} стратах, отобраны заново"];
    i2 [label="Два слепых судьи Codex:\\n{j1} и {j2}\\nтолько по пакету, κ = {dec(kappa)}"];
    i3 [label="Ложные принятия {pc(fa["estimate"])}\\nЛожные отказы {pc(fr["estimate"])}\\nСклейка OSM↔2GIS: чужая запись в {pc(mg["estimate"])}", {Q}];
    i1 -> i2 -> i3; }}
  subgraph cluster_ex {{ label="Существование: есть ли объект базы на самом деле"; fontname="Liberation Sans"; fontsize=11; color="#B8C2CF"; style="rounded,dashed";
    e1 [label="Выборка B: {lb["n"]} объектов\\n(по 145 на вид, по стратам)"];
    e2 [label="Аудитор-LLM ищет в открытом вебе\\nдатированное свидетельство;\\nметки не зависят от правил конвейера"];
    e3 [label="Разрешено {lb["resolved"]} из {lb["n"]} ({pc(lb["resolved"] / lb["n"], 0)})\\n→ результат — границы,\\nа не одно число", {Q}];
    e1 -> e2 -> e3; }}
  h [label="Ручная проверка (звонок, визит) — не выполнена:\\nвсе числа условны относительно моделей", {NO}];
  p -> i1; p -> e1; i3 -> h [style=dashed]; e3 -> h [style=dashed];
}}"""


def render(dot: str, path: Path) -> Path:
    subprocess.run(["dot", "-Tpng", "-o", str(path)], input=dot.encode("utf-8"), check=True)
    return path


# --------------------------------------------------------------------- docx


class Doc:
    """python-docx with the few things the report needs: **bold** runs, lists, tables, figures."""

    def __init__(self, title: str, subtitle: str):
        self.d = Document()
        sec = self.d.sections[0]
        sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(sec, side, Cm(2.0))
        st = self.d.styles
        st["Normal"].font.name = "Arial"
        st["Normal"].font.size = Pt(11)
        st["Normal"].element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
        st["Normal"].paragraph_format.space_after = Pt(6)
        for name, size, color in (("Title", 18, "1F2D3D"), ("Heading 1", 14, "1F2D3D"), ("Heading 2", 12, "33475B")):
            s = st[name]
            s.font.name, s.font.size, s.font.bold = "Arial", Pt(size), True
            s.font.color.rgb = RGBColor.from_string(color)
            s.element.rPr.rFonts.set(qn("w:asciiTheme"), "")  # the theme font would win over Arial
            s.element.rPr.rFonts.set(qn("w:hAnsiTheme"), "")
            s.paragraph_format.space_before = Pt(0 if name == "Title" else 14)
            s.paragraph_format.space_after = Pt(6)
            s.paragraph_format.keep_with_next = True
        self.d.add_paragraph(title, style="Title")
        p = self.d.add_paragraph()
        r = p.add_run(subtitle)
        r.font.size, r.font.color.rgb = Pt(10), RGBColor(0x55, 0x55, 0x55)
        self._border(p)
        self.fig_no = 0

    @staticmethod
    def _border(p):
        ppr = p._p.get_or_add_pPr()
        b = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "6"), ("w:color", "5B6B7F")):
            bottom.set(qn(k), v)
        b.append(bottom)
        ppr.append(b)

    @staticmethod
    def _runs(p, text: str, size: float | None = None, color: str | None = None):
        for part in re.split(r"(\*\*[^*]+\*\*)", text):
            if not part:
                continue
            r = p.add_run(part[2:-2] if part.startswith("**") else part)
            r.bold = part.startswith("**")
            if size:
                r.font.size = Pt(size)
            if color:
                r.font.color.rgb = RGBColor.from_string(color)

    def h1(self, t):
        self.d.add_paragraph(t, style="Heading 1")

    def p(self, t):
        self._runs(self.d.add_paragraph(), t)

    def note(self, t):
        self._runs(self.d.add_paragraph(), t, 9.5, "555555")

    def bullet(self, t):
        self._runs(self.d.add_paragraph(style="List Bullet"), t)

    def num(self, t):
        self._runs(self.d.add_paragraph(style="List Number"), t)

    def table(self, head: list[str], rows: list[list[str]], widths: list[float], numeric=(), bold_last=False):
        t = self.d.add_table(rows=1, cols=len(head))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False  # LibreOffice and Word read the grid widths, not the cells'
        for i, w in enumerate(widths):
            t.columns[i].width = Cm(w)
        for i, h in enumerate(head):
            self._cell(t.rows[0].cells[i], h, widths[i], True, i in numeric, "E3ECF7")
        for k, row in enumerate(rows):
            cells = t.add_row().cells
            last = bold_last and k == len(rows) - 1
            for i, v in enumerate(row):
                self._cell(cells[i], str(v), widths[i], last, i in numeric, "F2F4F7" if last else None)
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)

    def _cell(self, cell, text, width_cm, bold, right, fill):
        cell.width = Cm(width_cm)
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        if right:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        self._runs(p, f"**{text}**" if bold and "**" not in text else text, 9.5)
        if fill:
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), fill)
            cell._tc.get_or_add_tcPr().append(shd)

    def figure(self, path: Path, caption: str, width_cm: float):
        self.fig_no += 1
        p = self.d.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True
        p.add_run().add_picture(str(path), width=Cm(width_cm))
        c = self.d.add_paragraph()
        c.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = c.add_run(f"Рисунок {self.fig_no}. {caption}")
        r.italic, r.font.size, r.font.color.rgb = True, Pt(9.5), RGBColor(0x44, 0x44, 0x44)

    def save(self, path: Path):
        self.d.save(str(path))


# --------------------------------------------------------------------- the two documents


def method_doc(x: Numbers, figs: dict[str, Path], path: Path, stamp: str) -> None:
    d = Doc(
        "Методика проверки: общепит, алкоголь, табак",
        "Архангельск, Северодвинск, Новодвинск · как сформирована единая база · состояние на " + stamp,
    )
    d.h1("1. Что проверялось")
    d.p(
        "Подрядчик сдал девять выгрузок: три города (Архангельск, Северодвинск, Новодвинск) по трём видам — "
        "общепит, точки продажи алкоголя, точки продажи табака. Каждый объект проверен по независимым "
        "источникам. В копию каждой выгрузки добавлены **«Подтверждено»** (существует ли объект), "
        "**«Общепит?» / «Алкоголь?» / «Табак?»** (того ли он вида) и **«Основания»** (где найден, со ссылками, "
        "а после знака « || » — что найдено, но не засчитано, и почему). Из проверенных объектов собрана "
        "**единая база**: строка на объект, флаги источников, отметка «В базу»."
    )
    rows = []
    for _, cat in CATS:
        t = x.total(cat)
        rows.append(
            [
                cat,
                n(t["Строк в выгрузке"]),
                n(t["Объектов после склейки"]),
                f"{n(t['Подтверждено (2+ источника)'])} ({pc(t['Подтверждено (2+ источника)'] / t['Объектов после склейки'])})",
                n(t["В базу"]),
            ]
        )
    tot = {c: x.tot(c) for c in ("Строк в выгрузке", "Объектов после склейки", "Подтверждено (2+ источника)", "В базу")}
    rows.append(
        [
            "Всего",
            n(tot["Строк в выгрузке"]),
            n(tot["Объектов после склейки"]),
            f"{n(tot['Подтверждено (2+ источника)'])} ({pc(tot['Подтверждено (2+ источника)'] / tot['Объектов после склейки'])})",
            n(tot["В базу"]),
        ]
    )
    d.table(
        ["Вид", "Строк выгрузки", "Объектов", "Подтверждено", "В базу"],
        rows,
        [3.2, 3.1, 2.8, 4.0, 2.9],
        numeric=(1, 2, 3, 4),
        bold_last=True,
    )
    d.note(
        "По городам — лист «Этапы» в «база/единая_база.xlsx». Доля «Подтверждено» описывает работу правила, "
        "а не его точность; точность оценена отдельно (документ «Оценка качества проверки»)."
    )

    d.h1("2. Алгоритм")
    d.figure(figs["pipeline"], "Путь объекта от строки выгрузки до базы", 12.5)
    d.num(
        "**Строка → запись → объект.** Подрядчик повторяет одну вывеску под каждым возможным типом («кафе», "
        "«ресторан», «столовая»), поэтому строки сводятся к записям «вывеска в точке». Записи склеиваются в "
        "объект, если это одна вывеска от двух провайдеров (OSM и 2GIS в 50 м с тем же именем) или если они "
        "засчитали одну и ту же карточку источника или одну лицензию. Все итоги считаются по объектам."
    )
    d.num(
        "**Поиск в источниках.** Для каждого объекта ищется запись в шести источниках (раздел 3). Находка "
        "засчитывается, только если она **сильная** (именно этот объект: совпали имя и место) и **актуальная** "
        "(карточка не закрыта, запись не устарела, лицензия действует)."
    )
    d.num(
        "**Правило двух групп.** «Подтверждено» = 1, если засчитаны находки не меньше чем двух независимых "
        "групп источников. Одна группа даёт один голос, сколько бы записей в ней ни нашлось."
    )
    d.num(
        "**Вид — отдельно.** Того ли вида объект, решается по описаниям из источников, но не по классу "
        "подрядчика. С существованием в один показатель вид не складывается."
    )
    d.num(
        "**База и ручная проверка.** В базу идёт объект, который подтверждён, вид которого не «нет» и которого "
        "наш запрос не видит в Яндексе с пометкой «Больше не работает» (если другой карточки Яндекса за объект "
        "не засчитано). Спорные объекты вынесены на лист «На ручную проверку» с адресом, ссылкой и пустыми "
        "колонками для решения (раздел 7)."
    )

    d.h1("3. Источники")
    d.table(
        ["Группа", "Откуда", "Когда засчитывается"],
        [
            ["OSM", "флаг в выгрузке подрядчика", "запись подрядчика из OSM (нами не перечитывается)"],
            ["2GIS", "флаг в выгрузке подрядчика", "запись подрядчика из 2GIS (нами не перечитывается)"],
            [
                "Яндекс Карты",
                "наш запрос по каждому именованному объекту; отметка подрядчика «Найдено»",
                "похожее имя (сходство ≥ 72 из 100) в 150 м, для общей вывески в 60 м; карточка не «Больше не "
                "работает» (рисунок 2)",
            ],
            [
                "Overture Places",
                "открытый слой мест",
                "имя ≥ 85, 150 м, уверенность ≥ 0,5; место, собранное только из Foursquare, считается Foursquare",
            ],
            [
                "Foursquare OS Places",
                "открытый снимок",
                "как Overture, и запись обновлена не раньше 2022 года и не закрыта",
            ],
            [
                "Лицензии ЕГАИС",
                "реестр ФСРАР, снимок 01.07.2026",
                "общепит и алкоголь: своя действующая лицензия с совпавшим именем (рисунок 3); для табака не источник",
            ],
        ],
        [3.2, 4.8, 8.0],
    )
    d.p(
        "Сама выгрузка (название, тип подрядчика) в существование не засчитывается никогда: по ней определяется "
        "только вид. Колонка реестра администрации (Arkh_admin) предусмотрена для общепита, но во всех девяти "
        "файлах пуста и голосов не дала. OSM и 2GIS засчитаны по флагам подрядчика. Подтверждённых объектов, "
        "у которых засчитаны только эти два флага и ни одного внешнего источника: "
        + ", ".join(f"{cat.lower()} {n(x.only_osm_gis(cat))}" for _, cat in CATS)
        + "."
    )
    d.p(
        "Сколько объектов базы получили голос каждой группы (группы не складываются: у каждого объекта базы их "
        "не меньше двух):"
    )
    head = ["Вид", *[lab for _, lab in SOURCE_FLAGS], "В базу"]
    rows = []
    for _, cat in CATS:
        b = x.cat_rows(cat)
        b = b[b["В базу"] == 1]
        rows.append(
            [
                cat,
                *[("—" if col == "egais" and cat == "Табак" else n(b[col].sum())) for col, _ in SOURCE_FLAGS],
                n(len(b)),
            ]
        )
    d.table(head, rows, [2.4, 1.8, 1.8, 1.8, 1.9, 2.2, 1.8, 2.3], numeric=tuple(range(1, 8)))

    d.h1("4. Яндекс Карты")
    d.p(
        "Программа задаёт тот же запрос, что набрал бы человек: «название, город, адрес», с картой по координате "
        "объекта, и читает организации со страницы ответа. Каждый ответ сохраняется в кэш с датой, поэтому "
        "пересчёт не обращается к сети, а любой вердикт можно перепроверить по ссылке."
    )
    d.figure(figs["yandex"], "Как засчитывается Яндекс", 16.5)
    conf_now = x.tot("Подтверждено (2+ источника)")
    d.p(
        "Четыре правила этой схемы перенесены из проверки медицины, продуктовых, образования и спорта (24.11): "
        "закрытая карточка снимает «Найдено» подрядчика; одна карточка подтверждает одну точку; второй запрос для "
        "адреса без номера дома; запрос для каждого именованного объекта, а не только там, где от Яндекса зависел "
        f"вердикт. Подтверждённых объектов после них стало {n(conf_now)} вместо {n(x.build_2211)}. Каждый "
        "сдвинувшийся объект просмотрен вручную."
    )
    dates = x.base["Яндекс: дата проверки"].dropna().astype(str).str[:10]
    d.p(
        f"**Яндекс — снимок на дату.** Наши запросы прочитаны с {dates.min()} по {dates.max()}; дата стоит у "
        "каждого объекта в колонке «Яндекс: дата проверки». Известный дефект: имена сравниваются без "
        "транслитерации («Marshall Grill» и «Маршал гриль» — разные имена), поэтому часть верных карточек "
        "отвергается (документ «Оценка качества», раздел 1). Если от этого объект остаётся с одним источником, "
        "он попадает на лист ручной проверки."
    )

    d.h1("5. Лицензии ЕГАИС")
    d.p(
        "Реестр называет владельца лицензии, а не магазин: у сети один номер лицензии на сотню точек. Поэтому "
        "лицензия засчитывается, только если она **своя, действует и имя совпадает**. Лицензия, найденная лишь "
        "по адресу, слабая: в торговом центре по одному адресу десятки арендаторов."
    )
    d.figure(figs["egais"], "Как засчитывается лицензия", 13.5)
    ar = x.table[(x.table["Категория"] == "алкоголь")]
    egais_alc = int(ar["Источник: ЕГАИС"].sum())
    weak_alc = int(
        x.weak[x.weak["Категория"] == "алкоголь"]["ЕГАИС: своя действующая лишь по адресу/общей вывеске"].sum()
    )
    tob = x.weak[x.weak["Категория"] == "табак"]
    probable = "Табак: своя действующая розничная алкогольная лицензия (probable)"
    d.p(
        f"В алкоголе трёх городов лицензия засчитана {n(egais_alc)} объектам, а ещё {objs(weak_alc)} с "
        "действующей лицензией по адресу голоса не получили: имя не совпало. **Табак**: в реестре нет табачного "
        "класса, а ни один открытый источник не перечисляет точки продажи табака. Своя действующая алкогольная "
        "лицензия у табачной точки показывается после « || » как косвенный довод ("
        + ", ".join(f"{r['Город']} {n(r[probable])}" for _, r in tob.iterrows())
        + " объектов) и не складывается с прямыми свидетельствами."
    )

    d.h1("6. Проверка вида")
    d.p(
        "Описание вида берётся из названия (2GIS пишет «Йоко, суши-бар»), из рубрики той карточки Яндекса, которую "
        "сопоставила проверка, из категорий Foursquare и Overture, а для алкоголя ещё из своей лицензии. Описание "
        "проходит словари по порядку: сначала отсекается то, что точно не этот вид (аптека не продаёт алкоголь, "
        "«кафедра» не кафе), затем ищутся слова вида."
    )
    kinds = ["да", "нет", "противоречие", "автомат", "нет данных"]
    rows = []
    for _, cat in CATS:
        v = x.cat_rows(cat)["Вид?"].fillna("нет данных").value_counts()
        rows.append(
            [
                KIND_COL[cat],
                n(len(x.cat_rows(cat))),
                *[n(v.get(k, 0)) if (k != "автомат" or cat == "Общепит") else "—" for k in kinds],
            ]
        )
    d.table(["Вид", "Объектов", *kinds], rows, [2.6, 2.2, 1.9, 1.9, 2.8, 2.2, 2.4], numeric=tuple(range(1, 7)))
    no_conf = {cat: int(x.total(cat)["из них вид «нет»"]) for _, cat in CATS}
    tob_nd = x.cat_rows("Табак")["Вид?"].fillna("нет данных").eq("нет данных").mean()
    d.bullet(
        f"**Общепит «нет»** — в основном фирмы, магазины и клубы, выгруженные подрядчиком как общепит. "
        f"Существовать они могут, но в базу не идут ({n(no_conf['Общепит'])} подтверждённых)."
    )
    d.bullet(
        "**Алкоголь**: продуктовый магазин или кафе — это «нет данных», а не «да»: продавать алкоголь он может, "
        "но это догадка."
    )
    d.bullet(
        f"**Табак**: «нет данных» у {pc(tob_nd, 0)} объектов — честный ответ, а не пропуск. Большая часть "
        "табачной выгрузки — предположение подрядчика (тип «Inferred»: тип объекта совместим с продажей табака)."
    )

    d.h1("7. Единая база и ручная проверка")
    d.p(
        "Формат базы тот же, что у медицины, продуктовых, образования и спорта: строка на объект, флаги источников с "
        "именами базы 2021 года (OSM, GIS, yandex, overture, fsq, egais), «В базу», «Почему не в базе» и «Что "
        "проверить вручную»."
    )
    rows = []
    for _, cat in CATS:
        t = x.total(cat)
        rows.append(
            [
                cat,
                n(t["Объектов после склейки"]),
                n(t["Подтверждено (2+ источника)"]),
                n(t["из них вид «нет»"]),
                n(t["Исключено: Яндекс «Больше не работает»"]),
                n(t["В базу"]),
            ]
        )
    d.table(
        ["Вид", "Объектов", "Подтверждено", "из них вид «нет»", "Яндекс: «Больше не работает»", "В базу"],
        rows,
        [2.4, 2.4, 2.8, 2.8, 3.4, 2.2],
        numeric=(1, 2, 3, 4, 5),
    )
    rows = []
    for key, label in MANUAL.items():
        rows.append([label, *[n(x.manual(cat).get(key, 0)) for _, cat in CATS]])
    rows.append(
        [
            "Всего на листе «На ручную проверку»",
            *[n(x.cat_rows(cat)["Что проверить вручную"].notna().sum()) for _, cat in CATS],
        ]
    )
    d.table(
        ["Что проверить вручную", "Общепит", "Алкоголь", "Табак"],
        rows,
        [10.0, 2.0, 2.0, 2.0],
        numeric=(1, 2, 3),
        bold_last=True,
    )
    mr = x.merge_review
    d.p(
        "Два списка не влияют на вердикт: «Возможные дубли» в книге каждого вида и «к_проверке_склейки_OSM_2GIS.xlsx» — "
        f"{objs(len(mr), 'подтверждённых')}, где подрядчик склеил запись OSM и запись 2GIS с несхожими именами "
        f"(возможно, два заведения в одном доме); {n(mr['Только OSM + 2GIS'].sum())} из них держатся только на этих "
        "двух флагах."
    )

    d.h1("8. Общее и различия с проверкой медицины, продуктовых, образования и спорта")
    d.table(
        ["", "Общепит, алкоголь, табак", "Медицина, продуктовые, образование, спорт"],
        [
            [
                "Правило",
                "два независимых источника; Яндекс — запрос по каждому объекту с учётом «Больше не работает»",
                "то же",
            ],
            [
                "Государственный реестр",
                "лицензии ЕГАИС (общепит, алкоголь); у табака реестра нет",
                "Росздравнадзор (медицина)",
            ],
            [
                "Сопоставление по номеру, вхождению названия, адресу",
                "не включено: в торговле и общепите вхождение названия даёт чужие пары («Магнит» / «Магнит Косметик»), "
                "а общая вывеска («Продукты») по правилу слабая",
                "включено для типовых названий («Школа № 5», «Аптека»)",
            ],
            ["Уличные объекты по одному OSM", "нет", "уличный спорт"],
            [
                "Проверка качества",
                "ручной разметки нет: выборка с аудитором и две слепые проверки сопоставления",
                "сверка с ручной разметкой медицины Архангельска",
            ],
        ],
        [3.6, 6.2, 6.2],
    )

    d.h1("9. Чего результаты не утверждают")
    d.bullet("«Подтверждено = 1» не значит, что объект работает сегодня и что он того вида; это два разных вопроса.")
    d.bullet(
        "«Подтверждено = 0» — очередь на проверку, не приговор: у неподтверждённых не больше одного засчитанного "
        "источника, как правило, собственный провайдер выгрузки. Объект может быть закрыт, а может быть "
        "неизвестен Яндексу."
    )
    d.bullet("Подтверждение табачной точки — подтверждение существования места, а не продажи табака.")
    d.bullet(
        "Числа относятся к этим девяти выгрузкам на даты чтения источников; перенос на другие города и даты — "
        "экстраполяция."
    )
    d.note(
        "Подробная методология со ссылками на файлы и конфигурацию — methodology.md в этой папке. Код — "
        "репозиторий infra_validation, конфигурация config/finalest.yaml; документ собран "
        "workflow/scripts/report_docx.py из файлов базы."
    )
    d.save(path)


def quality_doc(x: Numbers, figs: dict[str, Path], path: Path, stamp: str) -> None:
    m = x.iv_manifest
    d = Doc(
        "Оценка качества проверки: общепит, алкоголь, табак",
        "Архангельск, Северодвинск, Новодвинск · метрики единой базы · состояние на " + stamp,
    )
    d.p(
        "Для общепита, алкоголя и табака ручной разметки руководителя нет, поэтому качество проверено двумя "
        "независимыми способами на **текущих** вердиктах и на самой базе. Они отвечают на разные вопросы, и их числа "
        "не складываются."
    )
    d.figure(figs["quality"], "Две оценки качества", 12.5)
    d.bullet(
        "**Сопоставление**: верно ли программа связывает объект с записями своих источников (кэш Яндекса, снимки "
        "Overture и Foursquare, реестр ЕГАИС, склейки подрядчика)."
    )
    d.bullet(
        "**Существование**: есть ли объект базы на самом деле. Верно сопоставленная устаревшая карточка — верное "
        "сопоставление, но ложное существование."
    )

    d.h1("1. Существуют ли объекты базы")
    lb = x.labels_b
    d.p(
        f"Выборка B: {objs(lb['n'])}, по 145 на вид, расслоенная по городам и числу источников. По каждому "
        "объекту языковая модель-аудитор искала в открытом вебе свидетельство с датой (живая карточка, сайт, "
        "новость) и ставила «существует / нет / неясно» по строгому правилу: недатированное зеркало справочника "
        "или страница чужого города не считаются. Метки описывают сам объект и от правил программы не зависят, "
        "поэтому они применены к **текущей** базе без новых запросов; веса выборки прежние."
    )
    d.p(
        f"**Главное: для {pc(1 - lb['resolved'] / lb['n'], 0)} объектов открытый веб не даёт независимого "
        f"датированного свидетельства** (разрешено {lb['resolved']} из {lb['n']}). Поэтому честный результат — "
        "границы: нижняя, если все неясные объекты считать против программы, верхняя — если в её пользу. Точечная "
        "оценка считается по разрешённым объектам, это объекты с заметным свежим следом в сети."
    )
    rows = [
        [
            "Объект базы существует — по разрешённым [95 % ДИ]",
            *[ext_cell(x.ext("base", c, "PPV existence")) for c, _ in CATS],
        ],
        [
            "то же — границы",
            *[
                rng(x.ext("base", c, "PPV existence")["unclear_low"], x.ext("base", c, "PPV existence")["unclear_high"])
                for c, _ in CATS
            ],
        ],
        [
            "объектов базы в выборке: разрешено / существует",
            *[
                f"{x.ext_counts[c]['resolved']} из {x.ext_counts[c]['in_base']} / {x.ext_counts[c]['exist']}"
                for c, _ in CATS
            ],
        ],
        [
            "Подтверждённый объект существует — по разрешённым",
            *[ext_cell(x.ext("current", c, "PPV existence")) for c, _ in CATS],
        ],
        [
            "Полнота подтверждения — границы",
            *[
                rng(
                    x.ext("current", c, "sensitivity existence")["unclear_low"],
                    x.ext("current", c, "sensitivity existence")["unclear_high"],
                )
                for c, _ in CATS
            ],
        ],
        [
            "Точность вида «да» (по разрешённым)",
            *[
                (
                    ext_cell(x.ext("current", c, "category precision yes"))
                    if x.ext("current", c, "category precision yes")["unclear_share"] <= 0.2
                    else "данных недостаточно"
                )
                for c, _ in CATS
            ],
        ],
        ["Неясных по существованию", *[pc(x.ext("base", c, "PPV existence")["unclear_share"], 0) for c, _ in CATS]],
    ]
    d.table(["Показатель", "Общепит", "Алкоголь", "Табак"], rows, [6.4, 3.2, 3.2, 3.2])
    base_hi = max(x.ext("base", c, "PPV existence")["estimate"] for c, _ in CATS)
    base_lo = min(x.ext("base", c, "PPV existence")["estimate"] for c, _ in CATS)
    cur = {c: x.ext("current", c, "PPV existence")["estimate"] for c, _ in CATS}
    bas = {c: x.ext("base", c, "PPV existence")["estimate"] for c, _ in CATS}
    d.p(
        f"Отбор в базу поднимает долю существующих по сравнению со всеми подтверждёнными: общепит "
        f"{dec(cur['catering'])} → {dec(bas['catering'])}, алкоголь {dec(cur['alcohol'])} → {dec(bas['alcohol'])}, "
        f"табак {dec(cur['tobacco'])} → {dec(bas['tobacco'])}: из базы ушли объекты не своего вида и закрытые по "
        f"Яндексу. По разрешённым объектам существует {dec(base_lo)}–{dec(base_hi)} объектов базы, но интервалы "
        "широкие, а неясность не случайна, поэтому заголовок — границы."
    )
    sens = max(
        abs(x.ext("base", c, "PPV existence", a)["estimate"] - x.ext("base", c, "PPV existence")["estimate"])
        for c, _ in CATS
        for a in ("dependent_as_unclear", "without_yandex", "yandex_card_shared")
    )
    d.p(
        "Аудитор мог опираться на тот же Яндекс, что и программа. Три пересчёта без таких меток сдвигают долю "
        f"существующих в базе не больше чем на {dec(100 * sens, 1)} п. п."
    )

    d.h1("2. Сопоставление с источниками")
    j1, j2 = model(x.iv_judges["judge1"][0]), model(x.iv_judges["judge2"][0])
    kappa = x.iv_agree[(x.iv_agree["source"] == "all") & (x.iv_agree["question"] == "same_org")]["kappa"].iloc[0]
    d.p(
        f"Из текущего прогона заново отобрано {m['n_decisions']} решений «объект ↔ запись источника» в "
        f"{len(m['strata'])} стратах по всем девяти выгрузкам, включая решения новых правил Яндекса. Каждое решение "
        f"вслепую оценили два судьи — разные модели в Codex ({j1} и {j2}) — только по данным пакета, без вердикта программы. Согласие судей "
        f"κ = {dec(kappa)}; эталон — совпавшие ответы, остальное входит в границы. Доли взвешены на всю "
        "совокупность решений, интервал — 95 %."
    )
    srcs = [
        ("yandex", "Яндекс (наш запрос)"),
        ("fsq", "Foursquare"),
        ("overture", "Overture"),
        ("egais", "ЕГАИС"),
        ("relink", "Соседняя запись выгрузки"),
        ("yandex_contractor", "«Найдено» подрядчика"),
        ("own", "Все решения программы"),
    ]
    rows = [[lab, iv_cell(x.ivm(s, "false_accept")), iv_cell(x.ivm(s, "false_reject"))] for s, lab in srcs]
    rows.append(["Склейка OSM ↔ 2GIS подрядчика (не проверяется)", iv_cell(x.ivm("merge", "false_accept")), "—"])
    d.table(["Источник", "Принял чужую запись", "Отверг свою запись"], rows, [6.4, 4.8, 4.8])
    fa, fr, fro = x.ivm("own", "false_accept"), x.ivm("own", "false_reject"), x.ivm("own", "false_reject_open")
    yfr, yfro = x.ivm("yandex", "false_reject"), x.ivm("yandex", "false_reject_open")
    st = x.iv_strata[(x.iv_strata["reference"] == "consensus") & (x.iv_strata["metric"] == "false_accept")]
    fa_err = int(st[~st["stratum"].str.startswith("merge")]["errors"].sum())
    fa_res = int(st[~st["stratum"].str.startswith("merge")]["resolved"].sum())
    mv = x.iv_moved
    d.num(
        f"**Чужих записей программа почти не принимает**: ошибок {fa_err} на {fa_res} разрешённых принятий, "
        f"{pc(fa['estimate'])} [{dec(100 * fa['low'], 1)}; {dec(100 * fa['high'], 1)}]. Ложных подтверждений от "
        "сопоставления практически нет."
    )
    d.num(
        f"**Ошибается она в сторону отказа, и больше всего у Яндекса**: {pc(yfr['estimate'])} отвергнутых карточек "
        f"на деле та же организация ({pc(yfro['estimate'])}, если не считать закрытые). Причина — имена "
        "сравниваются без транслитерации и с полным дескриптором («Marshall Grill» ↔ «Маршал гриль», "
        "«БрауМастер» ↔ «BrauMeister», «Гастробар Мясо» ↔ «Мясо»). Это потерянные подтверждения, а не ложные: "
        f"из {mv['errors']} таких ошибок в выборке у {mv['one_source']} объект остался с одним источником, и "
        f"{mv['one_source_manual']} из них уже стоят на листе «На ручную проверку» (карточка с другим названием "
        f"в 60 м). По всей программе ложных отказов {pc(fr['estimate'])}, без закрытых {pc(fro['estimate'])}."
    )
    mg = x.ivm("merge", "false_accept")
    mr = x.merge_review
    d.num(
        f"**Слабое место — склейка подрядчика OSM ↔ 2GIS**, которую программа берёт на веру: в {pc(mg['estimate'])} "
        "проверенных склеек есть запись другой организации, почти всегда при несхожих именах. Подтверждений, "
        f"которые держатся только на флагах OSM и 2GIS, — {n(x.only_osm_gis())} из "
        f"{n(x.tot('Подтверждено (2+ источника)'))}; склейки с несхожими именами ({objs(len(mr))}) собраны в "
        "«к_проверке_склейки_OSM_2GIS.xlsx»."
    )
    eg = x.ivm("egais", "false_reject")
    d.num(
        f"**ЕГАИС**: принятия чисты, а отказы оценить почти нельзя: из {int(eg['n'])} отобранных отказов судьи "
        f"согласно разрешили {int(eg['resolved'])}. Чтобы решить остальные, нужно знать, какое юрлицо держит "
        "вывеску, а в реестре этого нет."
    )

    d.h1("3. Чего утверждать нельзя")
    d.bullet(
        "что известна точная доля существующих объектов базы: заголовок — границы, точечная оценка — по объектам "
        "со свежим следом в сети;"
    )
    d.bullet("что программа почти ничего не упускает: высокая полнота на выборке — свойство эталона, а не программы;")
    d.bullet("сравнения городов: разрешённых меток на это не хватает;")
    d.bullet("что известна точность вида для алкоголя и табака;")
    d.bullet("что эталон проверен человеком: судьи и аудитор — языковые модели, ручная проверка не выполнена.")

    d.h1("4. Что сделает оценку точной")
    total_manual = int(x.base["Что проверить вручную"].notna().sum())
    d.p(
        "Узкое место — эталон, а не сопоставление. Протокол предусматривает ручную проверку 30 объектов выборки B "
        "(звонок или визит) с порогом согласия κ ≥ 0,8; её стоит расширить неясными объектами без подтверждения. "
        f"Второй путь — решения руководителя на листах «На ручную проверку» ({objs(total_manual)}): они дадут "
        "разметку, по которой совпадение с программой считается так же, как для медицины. Третий — исправить "
        "сравнение имён Яндекса (транслитерация): это вернёт часть потерянных подтверждений, после чего базу "
        "нужно пересобрать и пересчитать эти метрики."
    )
    d.note(
        "Подробности, страты, анализы чувствительности и ограничения — methodology.md, разделы 5–9. Расчёт: "
        "pixi run calibration-current (выборка B на текущей базе), pixi run internal-validity --iv "
        "results/finalest/internal_validity_v2 (сопоставление)."
    )
    d.save(path)


def message(x: Numbers, n_books: int) -> str:
    mr = x.merge_review
    fa, fr = x.ivm("own", "false_accept"), x.ivm("own", "false_reject")
    mg = x.ivm("merge", "false_accept")
    lb = x.labels_b
    per = ", ".join(f"{cat.lower()} {n(x.total(cat)['В базу'])}" for _, cat in CATS)
    ppv = "; ".join(
        f"{cat.lower()} {dec(x.ext('base', c, 'PPV existence')['estimate'])} "
        f"(границы {rng(x.ext('base', c, 'PPV existence')['unclear_low'], x.ext('base', c, 'PPV existence')['unclear_high'])})"
        for c, cat in CATS
    )
    total_manual = int(x.base["Что проверить вручную"].notna().sum())
    return f"""Здравствуйте, Александр Валерьевич!

Выложил на Яндекс Диск итоговую проверку общепита, алкоголя и табака (три города): <ССЫЛКА>

В папке:
- «база»: единая база по всем девяти выгрузкам (строка на объект, флаги OSM, GIS, yandex, overture, fsq, egais, отметка «В базу», лист «Этапы» с числами по городам) и по книге на вид с листом «На ручную проверку» (по адресу, со ссылкой на Яндекс и колонками для решения);
- «Методика»: два документа со схемами — «Методика проверки» (как сформирована база: алгоритм, источники, правила для Яндекса и лицензий ЕГАИС, проверка вида) и «Оценка качества проверки» (метрики базы и чего по ним утверждать нельзя);
- «data»: {n_books} выгрузок подрядчика с добавленными колонками «Подтверждено», вид и «Основания» (что засчитано со ссылками; после « || » — что найдено, но не засчитано, и почему);
- «сводка.xlsx» — числа по городам и видам; «к_проверке_склейки_OSM_2GIS.xlsx» — {objs(len(mr), "подтверждённых")}, где подрядчик склеил OSM и 2GIS с несхожими названиями.

Итог: из {n(x.tot("Строк в выгрузке"))} строк получилось {objs(x.tot("Объектов после склейки"))}, подтверждено двумя независимыми источниками {n(x.tot("Подтверждено (2+ источника)"))}, в базу вошли {n(x.tot("В базу"))}: {per}. На ручную проверку вынесено {objs(total_manual, "спорных")}.

Что поменялось с прошлой версии: по примеру проверки медицины Яндекс теперь запрашивается по каждому объекту с названием, учитывается «Больше не работает» (в том числе на карточке, которую засчитал подрядчик), одна карточка подтверждает одну точку. Подтверждённых стало {n(x.tot("Подтверждено (2+ источника)"))} вместо {n(x.build_2211)}, каждый сдвинувшийся объект я просмотрел.

Отличие от медицины: для алкоголя и общепита вторым государственным источником служит реестр лицензий ЕГАИС; лицензия засчитывается, только если своя, действует и совпало название, а не просто адрес. У табака реестра нет, поэтому алкогольная лицензия табачной точки показывается как косвенный довод и не засчитывается.

Качество. Вашей ручной разметки по этим видам нет, поэтому я оценил текущую базу двумя слепыми проверками:
- существование: по выборке из {lb["n"]} объектов с меткой аудитора доля существующих среди объектов базы — {ppv}. Границы широкие, потому что для {pc(1 - lb["resolved"] / lb["n"], 0)} объектов в открытом вебе нет свежего датированного следа; сузить их можно только ручной проверкой;
- сопоставление: {x.iv_manifest["n_decisions"]} решений заново оценили два слепых судьи. Чужих записей программа почти не принимает ({pc(fa["estimate"])}), чаще отвергает свои ({pc(fr["estimate"])}), в основном в Яндексе из-за транслитерации названий («Marshall Grill» и «Маршал гриль»); такие объекты, если остались с одним источником, стоят на листе ручной проверки. Слабое место — склейки OSM и 2GIS у подрядчика: в {pc(mg["estimate"], 0)} проверенных склеек оказалась запись другой организации; подтверждений, которые держатся только на этих двух флагах, {n(x.only_osm_gis())}.

Если отметите решения на листах «На ручную проверку», посчитаю совпадение так же, как для медицины.

Таблицы содержат данные 2ГИС и отметки Яндекса, поэтому публиковать их нельзя, пока не подтверждены лицензии.

Код — в том же репозитории.
"""


def build(
    out: Path,
    base_dir: Path,
    table: pd.DataFrame,
    weak: pd.DataFrame,
    build_2211: int,
    merge_review: pd.DataFrame,
    stamp: str,
    n_books: int,
) -> list[Path]:
    """`stamp` is the base's YYMMDD prefix; the documents show it as a date."""
    stamp = f"{stamp[4:6]}.{stamp[2:4]}.20{stamp[0:2]}" if re.fullmatch(r"\d{6}", stamp) else stamp
    x = collect(out, base_dir, table, weak, build_2211, merge_review)
    folder = out / FOLDER
    folder.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        figs = {
            "pipeline": render(dot_pipeline(x), t / "pipeline.png"),
            "yandex": render(dot_yandex(), t / "yandex.png"),
            "egais": render(dot_egais(), t / "egais.png"),
            "quality": render(dot_quality(x), t / "quality.png"),
        }
        method_doc(x, figs, folder / METHOD_DOC, stamp)
        quality_doc(x, figs, folder / QUALITY_DOC, stamp)
    (out / MESSAGE).write_text(message(x, n_books), encoding="utf-8")
    return [folder / METHOD_DOC, folder / QUALITY_DOC, out / MESSAGE]


__all__ = ["build", "collect", "Numbers", "np"]
