"""Build and validate the blind human review workbook for auditor labels."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

ANSWER_COLUMNS = ("exists", "open", "category_ok", "is_vending")
VALID_ANSWERS = {
    "exists": ("yes", "no", "unclear"),
    "open": ("open", "closed", "unclear"),
    "category_ok": ("yes", "no", "unclear"),
    "is_vending": ("yes", "no", "unclear"),
}
INPUT_COLUMNS = ("unit_id", "name", "address", "city", "claimed_category", "lat", "lon")
SEARCH_COLUMNS = ("yandex_search", "2gis_search", "google_search")
HEADERS = (*INPUT_COLUMNS, *SEARCH_COLUMNS, *ANSWER_COLUMNS)


def select_rows(sample: pd.DataFrame, blind: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Select 30 B units while meeting the protocol's category and diagnostic minima."""
    if sample.unit_id.duplicated().any() or blind.unit_id.duplicated().any() or labels.unit_id.duplicated().any():
        raise ValueError("unit_id must be unique in each input")
    if "rank" not in sample:
        raise ValueError("sample_B must include sampler rank")
    required_labels = {"unit_id", *ANSWER_COLUMNS}
    if not required_labels.issubset(labels.columns):
        raise ValueError(f"Missing auditor label columns: {sorted(required_labels - set(labels.columns))}")
    merged = sample[["unit_id", "category", "ver_exists", "rank"]].merge(blind, on="unit_id", validate="one_to_one")
    merged = merged.merge(labels[["unit_id", *ANSWER_COLUMNS]], on="unit_id", validate="one_to_one")
    if len(merged) != len(sample):
        raise ValueError("Every B unit must have blind inputs and an auditor label")
    for axis in ANSWER_COLUMNS:
        if not merged[axis].isin(VALID_ANSWERS[axis]).all():
            raise ValueError(f"Every B unit must have a completed, valid {axis} label")
    merged["audit_E"] = merged.apply(
        lambda row: (
            "no" if row.exists == "no" or row.open == "closed" else "unclear" if row.exists == "unclear" else "yes"
        ),
        axis=1,
    )
    merged["closed"] = merged.open.eq("closed")
    merged["disagreement"] = merged.apply(
        lambda row: row.audit_E != "unclear" and (row.ver_exists == "conf") != (row.audit_E == "yes"), axis=1
    )
    merged["_rank"] = merged["rank"].astype(str)
    categories = sorted(merged.category.unique())
    if len(categories) != 3:
        raise ValueError(f"Expected three categories in B; found {len(categories)}")
    available = merged.audit_E.value_counts().to_dict()
    goals = {
        "yes": min(12, available.get("yes", 0)),
        "no": min(12, available.get("no", 0)),
        "unclear": min(6, available.get("unclear", 0)),
    }
    chosen: list[int] = []
    # Greedily cover overlapping protocol constraints; the sampler rank breaks ties.
    while len(chosen) < min(30, len(merged)):
        current = merged.loc[chosen]
        cat_counts = current.category.value_counts().to_dict()
        e_counts = current.audit_E.value_counts().to_dict()
        closed_count = int(current.closed.sum())
        disagreement_count = int(current.disagreement.sum())
        remaining = merged.loc[~merged.index.isin(chosen)].copy()

        def gain(
            row,
            cat_counts=cat_counts,
            e_counts=e_counts,
            closed_count=closed_count,
            disagreement_count=disagreement_count,
        ):
            score = int(cat_counts.get(row.category, 0) < 8)
            score += int(e_counts.get(row.audit_E, 0) < goals.get(row.audit_E, 0))
            score += 1.5 * int(row.closed and closed_count < 4)
            score += 1.5 * int(row.disagreement and disagreement_count < 8)
            return score

        remaining["_gain"] = remaining.apply(gain, axis=1)
        selected_index = remaining.sort_values(["_gain", "_rank", "unit_id"], ascending=[False, True, True]).index[0]
        chosen.append(selected_index)
    selected = merged.loc[chosen]
    unmet = [f"category {cat}" for cat in categories if int(selected.category.eq(cat).sum()) < 8]
    if len(selected) != 30:
        unmet.append("30 objects")
    if int(selected.closed.sum()) < 4:
        unmet.append("four closed objects")
    if int(selected.disagreement.sum()) < 8:
        unmet.append("eight pipeline disagreements")
    if unmet:
        raise ValueError("B cannot meet human review selection requirements: " + ", ".join(unmet))
    return selected.sort_values(["_rank", "unit_id"])[list(INPUT_COLUMNS)].reset_index(drop=True)


def write_workbook(sample_path, blind_path, labels_path, output_path):
    """Create the blind XLSX and add search links plus answer dropdowns."""
    sample = pd.read_csv(sample_path).fillna("")
    blind = pd.read_csv(blind_path).fillna("")
    labels = pd.read_csv(labels_path).fillna("")
    selected = select_rows(sample, blind, labels)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "human_check"
    sheet.append(HEADERS)
    for row in selected.itertuples(index=False, name=None):
        unit_id, name, address, city, claimed, lat, lon = row
        query = " ".join(str(x) for x in (name, address, city) if str(x).strip())
        sheet.append(
            [unit_id, name, address, city, claimed, lat, lon]
            + [
                f"https://yandex.ru/maps/?text={_url_quote(query)}",
                f"https://2gis.ru/search/{_url_quote(query)}",
                f"https://www.google.com/search?q={_url_quote(query)}",
                "",
                "",
                "",
                "",
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for col, answer in enumerate(ANSWER_COLUMNS, start=len(INPUT_COLUMNS) + len(SEARCH_COLUMNS) + 1):
        validation = DataValidation(type="list", formula1=f'"{",".join(VALID_ANSWERS[answer])}"', allow_blank=True)
        validation.error = "Выберите значение из списка."
        validation.errorTitle = "Недопустимая метка"
        validation.showErrorMessage = True
        sheet.add_data_validation(validation)
        validation.add(f"{_column_letter(col)}2:{_column_letter(col)}{sheet.max_row}")
    for row_number in range(2, sheet.max_row + 1):
        for col_number in range(len(INPUT_COLUMNS) + 1, len(INPUT_COLUMNS) + len(SEARCH_COLUMNS) + 1):
            cell = sheet.cell(row_number, col_number)
            cell.hyperlink = cell.value
            cell.style = "Hyperlink"
    workbook.save(output_path)
    return Path(output_path)


def _url_quote(value):
    from urllib.parse import quote

    return quote(value, safe="")


def _column_letter(number):
    from openpyxl.utils import get_column_letter

    return get_column_letter(number)


def read_filled_workbook(path) -> pd.DataFrame:
    """Read human labels, rejecting missing or out-of-schema answers."""
    sheet = load_workbook(path, data_only=True, read_only=True).active
    rows = list(sheet.values)
    if not rows or tuple(rows[0]) != HEADERS:
        raise ValueError("Unexpected human check workbook columns")
    frame = pd.DataFrame(rows[1:], columns=HEADERS).dropna(how="all")
    if frame.unit_id.isna().any() or frame.unit_id.astype(str).str.strip().eq("").any():
        raise ValueError("Missing unit_id")
    for column in ANSWER_COLUMNS:
        values = frame[column].fillna("").astype(str).str.strip()
        invalid = sorted(set(values) - set(VALID_ANSWERS[column]))
        if invalid:
            raise ValueError(f"Invalid {column} answer(s): {invalid}")
        if values.eq("").any():
            raise ValueError(f"Missing {column} answer")
        frame[column] = values
    if frame.unit_id.duplicated().any():
        raise ValueError("Duplicate unit_id in human check workbook")
    return frame[["unit_id", *ANSWER_COLUMNS]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", default="B", choices=["B"])
    parser.add_argument("--input-dir", default="results/finalest/calibration")
    parser.add_argument("--output", default="results/finalest/calibration/human_check_B.xlsx")
    args = parser.parse_args()
    root = Path(args.input_dir)
    write_workbook(root / "sample_B.csv", root / "auditor_input_B.csv", root / "auditor_labels_B.csv", args.output)


if __name__ == "__main__":
    main()
