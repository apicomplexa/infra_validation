"""The deliverable: a copy of the delivered workbook with the verdict in it.

The contractor's file goes back as it came -- same sheets, formatting, freeze
panes -- with the verdict columns in front («Подтверждено», «Основания»,
«Вид?»), the evidence appended after the contractor's own columns (rebuilt
from each check's `wide()`), and a legend sheet saying how to read them, with
the counts of this file.  It is edited with openpyxl rather than rewritten by
pandas for exactly that reason.  The raw file is never touched.

Which columns, their titles and widths, and the legend texts are the
category's `deliver` block in config.yaml.  Legend texts are `str.format`
templates over the Policies actually used, so a threshold moved in the config
is the threshold the reader is told about.

Ported from `arkhinfra.final_confirm.write_xlsx` (archive tag
`archive-2026-10-30`).
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import copy
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font
from openpyxl.utils import column_index_from_string, get_column_letter

# Columns the input adapter adds to the rows it read; everything else in the
# raw frame is a column of the delivered sheet (`inputs.final_xlsx.read_rows`).
ADAPTER_ROW_COLUMNS = {"entity", "site_key"}


def sheet_columns(raw: pd.DataFrame) -> list[str]:
    """The delivered sheet's own columns, in order, from the rows the adapter read."""
    return [c for c in raw.columns if not str(c).startswith("_") and c not in ADAPTER_ROW_COLUMNS]


def legend(layout: Mapping, policies: Mapping[str, object], min_sources: int,
           min_sources_genitive: str) -> list[tuple[str, str]]:
    """The «Как читать» rows, thresholds interpolated from the Policies used."""
    ns = dict(policies, min_sources=min_sources, min_sources_genitive=min_sources_genitive)
    return [(k, v.format(**ns)) for k, v in layout["legend"]]


def output_name(src: Path | str, layout: Mapping) -> str:
    """`<original stem><suffix>.xlsx` -- the archive's `…_подтверждено.xlsx`; a CSV becomes a workbook."""
    return f"{Path(src).stem}{layout['suffix']}.xlsx"


def _cell(v):
    """A pandas / numpy value as openpyxl wants it; "" is an empty cell."""
    if isinstance(v, (np.integer, np.bool_)):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    if v is pd.NA:
        return None
    return None if isinstance(v, str) and v == "" else v


def workbook(sheet: pd.DataFrame, name: str) -> openpyxl.Workbook:
    """A delivered table that came as CSV, as a one-sheet workbook to edit like the XLSX ones.

    Header bold and frozen, cells as pandas read them ("" / NaN left empty).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = name
    ws.append([str(c) for c in sheet.columns])
    for row in sheet.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and np.isnan(v)) else _cell(v) for v in row])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    return wb


def write(src: Path | str, dst: Path | str, rows: pd.DataFrame, columns: list[str],
          summary: list[tuple[str, object]], legend_rows: list[tuple[str, str]], layout: Mapping) -> None:
    """Copy `src` to `dst` with the front columns, the appended evidence and the legend.

    `rows` is the delivered sheet as read plus the decided columns, row-aligned
    with the sheet; `columns` the sheet's own columns as pandas read them.  A
    `src` that is not an XLSX (the v4 CSV) is first made a workbook of its own
    columns (`workbook`), so the same insertion runs on it.  The
    header on the sheet is checked against `columns` and the row count against
    `rows`, which is what keeps the row order honest.
    """
    src, dst = Path(src), Path(dst)
    front, appended = layout["front"], layout["appended"]
    wb = openpyxl.load_workbook(src) if src.suffix.lower() == ".xlsx" else workbook(rows[columns], layout["sheet"])
    ws = wb[layout["sheet"]]
    header = [c.value for c in ws[1]]
    if header != list(columns):
        raise RuntimeError(f"{src.name}: header changed between reads")
    if ws.max_row - 1 != len(rows):
        raise RuntimeError(f"{src.name}: {ws.max_row - 1} rows on sheet, {len(rows)} read")

    style_src = ws.cell(1, 1)
    widths = {k: v.width for k, v in ws.column_dimensions.items()}
    ncol = ws.max_column
    nfront = len(front)
    ws.insert_cols(1, nfront)
    # insert_cols moves cells, not column widths: shift those by hand, right to left.
    for k, w in sorted(widths.items(), key=lambda kv: -column_index_from_string(kv[0])):
        ws.column_dimensions[get_column_letter(column_index_from_string(k) + nfront)].width = w

    def put_header(col: int, text: str, width: float) -> None:
        c = ws.cell(1, col, text)
        c.font, c.fill, c.alignment, c.border = (copy(style_src.font), copy(style_src.fill),
                                                 copy(style_src.alignment), copy(style_src.border))
        ws.column_dimensions[get_column_letter(col)].width = width

    for j, f in enumerate(front, start=1):
        put_header(j, f["title"], f["width"])
    first_new = ncol + nfront + 1
    for j, (title, *_) in enumerate(appended):
        put_header(first_new + j, title, layout.get("appended_width", 22))
    written = [(j, f["column"]) for j, f in enumerate(front, start=1) if f.get("write", True)]
    keys = [a[1] for a in appended]
    for i, r in enumerate(rows[[c for _, c in written] + keys].itertuples(index=False), start=2):
        vals = list(r)
        for (j, _), v in zip(written, vals[:len(written)], strict=True):
            c = ws.cell(i, j, _cell(v))
            if isinstance(c.value, str):
                c.alignment = Alignment(wrap_text=False)
        for j, v in enumerate(vals[len(written):]):
            ws.cell(i, first_new + j, _cell(v))
    last = get_column_letter(first_new + len(appended) - 1)
    ws.auto_filter.ref = f"A1:{last}{ws.max_row}"

    name = layout["legend_sheet"]
    if name in wb.sheetnames:
        del wb[name]
    lg = wb.create_sheet(name, 1)
    lg.column_dimensions["A"].width, lg.column_dimensions["B"].width = 26, 120
    lg.append(["Как читать", ""])
    for k, v in legend_rows:
        lg.append([k, v])
    lg.append([])
    lg.append(["Итоги", ""])
    for k, v in summary:
        lg.append([k, _cell(v)])
    for row in lg.iter_rows():
        row[0].font = Font(bold=True)
        if len(row) > 1:
            row[1].alignment = Alignment(wrap_text=True, vertical="top")
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
