"""The deliverable builder reads the pipeline's summary text; these pin its parsing."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "workflow" / "scripts" / "build_deliverable.py"
spec = importlib.util.spec_from_file_location("build_deliverable", SCRIPT)
bd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bd  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(bd)

TEXT = """== x_общепит.xlsx
Строк: 10
Объектов (уникальных вывесок в точке): 3 (записей 4)
Подтверждено объектов: 2 из 3 (66.7%); записей 3 из 4
Наша Яндекс-проверка: запрошено объектов: 2 (записей 2)
   closed: 1 (записей 1)
Foursquare: засчитано / найдено, но старое или закрыто (объектов): 1 / 0 (записей 2 / 0)

Источники объекта (комбинации):
sources
OSM; 2GIS                 1
2GIS; Яндекс; Foursquare  1
OSM                       1
"""


def test_parse_summary_labels_with_colons():
    s = bd.parse_summary(TEXT)
    assert s.title == "x_общепит.xlsx"
    assert s.values["Наша Яндекс-проверка: запрошено объектов"] == "2 (записей 2)"
    assert s.values["closed"] == "1 (записей 1)"
    assert bd.pairs(s.values["Foursquare: засчитано / найдено, но старое или закрыто (объектов)"]) == [(1, 2), (0, 0)]
    c = bd.confirmed(s.values["Подтверждено объектов"])
    assert (c["objects"], c["of"], c["pct"], c["records"], c["records_of"]) == (2, 3, "66.7", 3, 4)


def test_source_counts_are_per_group_not_summed():
    s = bd.parse_summary(TEXT)
    src = bd.source_counts(s.combos)
    assert src["OSM"] == 2 and src["2GIS"] == 2 and src["Яндекс"] == 1 and src["Foursquare"] == 1
    assert src["ЕГАИС"] == 0


def test_unknown_group_is_refused():
    with pytest.raises(ValueError):
        bd.source_counts({"OSM; Google": 1})


def test_internal_validity_cells_read_the_metrics_and_hide_small_domains():
    import pandas as pd

    m = pd.DataFrame([
        {"reference": "consensus", "source": "own", "metric": "false_accept", "sufficient": True, "bad": 0,
         "resolved": 116, "point": 0.0, "lo95": 0.0, "hi95": 0.063, "bound_lo": 0.0, "bound_hi": 0.032},
        {"reference": "consensus", "source": "relink", "metric": "false_reject", "sufficient": False, "bad": 1,
         "resolved": 4, "point": None, "lo95": None, "hi95": None, "bound_lo": 0.2, "bound_hi": 0.4},
    ])
    assert bd.iv_cell(m, "own", "false_accept") == "0,0 % [0,0; 6,3] (границы 0,0–3,2 %)"
    assert bd.iv_cell(m, "relink", "false_reject") == "данных недостаточно (1 из 4)"
    assert bd.iv_cell(m, "merge", "false_reject") == "—"
