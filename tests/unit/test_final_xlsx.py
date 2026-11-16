from __future__ import annotations

import pandas as pd
import pytest

from infracheck.categories.base import to_rows
from infracheck.inputs.final_xlsx import load, to_objects


def rows(types: list[str]) -> pd.DataFrame:
    n = len(types)
    return pd.DataFrame({
        "type": types,
        "city": ["Архангельск"] * n,
        "_name": ["Бристоль"] * n,
        "_addr": ["Ленина, 1"] * n,
        "latitude": [64.5] * n,
        "longitude": [40.5] * n,
        "site_key": ["Архангельск|Бристоль|64.5|40.5"] * n,
        "entity": [0] * n,
        "OSM": [1] * n,
        "GIS": [0] * n,
        "Arkh_admin": [0] * n,
        "_ids": [f"osm:node:{i + 1}" for i in range(n)],
        "Яндекс: статус": [""] * n,
        "Яндекс: название": [""] * n,
        "Яндекс: адрес": [""] * n,
        "Яндекс: ссылка": [""] * n,
    })


def test_risk_rows_remain_distinct_and_keep_type_metadata():
    from infracheck.inputs.v4_csv import A_CATERING, A_SHOP, T_HOOKAH, T_POSSIBLE

    alcohol, alcohol_map = to_objects(rows([A_SHOP, A_CATERING]), "alc", "20/arkhangelsk", "alcohol")
    assert len(alcohol) == 2
    assert alcohol["risk"].tolist() == ["Alcohol", "Alcohol"]
    assert alcohol["trade"].tolist() == ["off-trade", "on-trade"]
    assert alcohol["evidence"].tolist() == ["Inferred", "Inferred"]
    assert alcohol["brand_learn"].tolist() == [True, True]
    assert alcohol["n_rows"].tolist() == [1, 1]
    assert alcohol_map["object_id"].nunique() == 2

    tobacco, _ = to_objects(rows([T_POSSIBLE, T_HOOKAH]), "tob", "20/arkhangelsk", "tobacco")
    assert tobacco["risk"].tolist() == ["Tobacco", "Tobacco"]
    assert tobacco["trade"].tolist() == ["off-trade", "on-trade"]
    assert tobacco["evidence"].tolist() == ["Inferred", "Direct"]
    assert tobacco["brand_learn"].tolist() == [False, False]


def test_catering_keeps_legacy_site_grouping_and_metadata():
    catering, rowmap = to_objects(rows(["Кафе", "Ресторан"]), "food", "20/arkhangelsk", "catering")
    assert len(catering) == 1
    assert catering.iloc[0]["n_rows"] == 2
    assert catering.iloc[0]["osm"]
    assert "risk" not in catering
    assert rowmap["object_id"].nunique() == 1


def test_risk_adapter_rejects_unknown_and_cross_category_types():
    from infracheck.inputs.v4_csv import A_SHOP

    with pytest.raises(ValueError, match="unknown alcohol type"):
        to_objects(rows(["Новый неизвестный тип"]), "alc", "20/arkhangelsk", "alcohol")
    with pytest.raises(ValueError, match="other risk type"):
        to_objects(rows([A_SHOP]), "tob", "20/arkhangelsk", "tobacco")


def test_risk_load_broadcasts_matching_entity_to_rows(tmp_path):
    from infracheck.inputs.v4_csv import A_CATERING, A_SHOP

    types = [A_SHOP, A_CATERING]
    keys = {
        "type_orig": types,
        "name_orig": ["Бристоль"] * 2,
        "adress_orig": ["Ленина, 1"] * 2,
        "latitude": [64.5] * 2,
        "longitude": [40.5] * 2,
    }
    data = pd.DataFrame({
        "type": types,
        "city": ["Архангельск"] * 2,
        "name": ["Бристоль"] * 2,
        "address": ["Ленина, 1"] * 2,
        "latitude": [64.5] * 2,
        "longitude": [40.5] * 2,
        "OSM": [1, 1],
        "GIS": [0, 0],
        "Arkh_admin": [0, 0],
        "Яндекс: статус": ["", ""],
        "Яндекс: название": ["", ""],
        "Яндекс: адрес": ["", ""],
        "Яндекс: ссылка": ["", ""],
    })
    original = pd.DataFrame({**keys, "city_orig": ["Архангельск"] * 2})
    quality = pd.DataFrame({**keys, "providers": ["osm", "osm"],
                            "matched_source_ids": ["osm:node:1", "osm:node:2"]})
    path = tmp_path / "alcohol.xlsx"
    with pd.ExcelWriter(path) as writer:
        data.to_excel(writer, sheet_name="data", index=False)
        original.to_excel(writer, sheet_name="original_data", index=False)
        quality.to_excel(writer, sheet_name="quality", index=False)

    objects, rowmap, raw = load(path, "alc", "20/arkhangelsk", "alcohol")
    rows_out = to_rows(raw, rowmap, objects, ["entity", "risk", "trade"])
    assert len(objects) == 2
    assert raw["entity"].tolist() == rowmap["entity"].tolist() == [0, 1]
    assert rows_out["entity"].tolist() == [0, 1]
    assert rows_out["trade"].tolist() == ["off-trade", "on-trade"]
