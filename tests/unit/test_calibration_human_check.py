import pandas as pd
import pytest
from openpyxl import load_workbook

from infracheck.audit.human_check import HEADERS, read_filled_workbook, write_workbook


def fixture_files(tmp_path):
    units = []
    for category in ("catering", "alcohol", "tobacco"):
        for number in range(12):
            unit = f"{category}:{number}"
            units.append(
                {
                    "unit_id": unit,
                    "category": category,
                    "ver_exists": "conf" if number < 8 else "not",
                    "rank": f"{category}-{number:02}",
                    "name": f"Name {unit}",
                    "address": "Address",
                    "city": "Архангельск",
                    "claimed_category": category,
                    "lat": 64.5,
                    "lon": 40.5,
                    "exists": "no" if number < 4 or number >= 8 else "yes",
                    "open": "closed" if number in {8, 9} else "open",
                    "category_ok": "yes",
                    "is_vending": "unclear",
                }
            )
    all_data = pd.DataFrame(units)
    sample_path, blind_path, labels_path = (tmp_path / name for name in ("sample.csv", "blind.csv", "labels.csv"))
    all_data[["unit_id", "category", "ver_exists", "rank"]].to_csv(sample_path, index=False)
    all_data[["unit_id", "name", "address", "city", "claimed_category", "lat", "lon"]].to_csv(blind_path, index=False)
    all_data[["unit_id", "exists", "open", "category_ok", "is_vending"]].to_csv(labels_path, index=False)
    return sample_path, blind_path, labels_path


def test_workbook_is_blind_has_links_and_answer_validation(tmp_path):
    sample, blind, labels = fixture_files(tmp_path)
    output = tmp_path / "human_check_B.xlsx"
    write_workbook(sample, blind, labels, output)

    workbook = load_workbook(output)
    sheet = workbook.active
    assert tuple(cell.value for cell in sheet[1]) == HEADERS
    assert sheet.max_row == 31
    assert not {"ver_exists", "category", "ver_kind", "stratum"} & set(HEADERS)
    assert all(sheet.cell(2, column).hyperlink for column in (8, 9, 10))
    assert len(sheet.data_validations.dataValidation) == 4
    assert len({sheet.cell(row, 1).value for row in range(2, 32)}) == 30


def test_read_filled_workbook_validates_labels(tmp_path):
    sample, blind, labels = fixture_files(tmp_path)
    output = tmp_path / "human.xlsx"
    write_workbook(sample, blind, labels, output)
    book = load_workbook(output)
    sheet = book.active
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 11, "yes")
        sheet.cell(row, 12, "open")
        sheet.cell(row, 13, "yes")
        sheet.cell(row, 14, "unclear")
    book.save(output)
    answers = read_filled_workbook(output)
    assert len(answers) == 30
    assert answers.exists.eq("yes").all()

    sheet = load_workbook(output).active
    sheet["K2"] = "maybe"
    book = sheet.parent
    book.save(output)
    with pytest.raises(ValueError, match="Invalid exists"):
        read_filled_workbook(output)
