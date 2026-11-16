"""Adapter for the final delivery: one XLSX per city and object class.

The workbook's sheets: `data` (the deliverable: provider flags `OSM`, `GIS`,
`Яндекс: найдено`, `Arkh_admin`, then the object, then the contractor's Yandex
check), `original_data`, `quality`, and summaries.  This module reads the file
and groups its rows into objects; it **decides nothing** -- re-judging the
contractor's Yandex hit, relinking providers and the rest belong to the checks.

Ported from `arkhinfra.final_confirm.load` / `objects` (archive tag
`archive-2026-10-30`).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from infracheck.core import entities, objects
from infracheck.core.names import is_generic

DATA_SHEET = "data"
STATUS_FOUND = "Найдено"

# Columns of the ObjectTable beyond the contract, produced here.
ADAPTER_COLUMNS = ["entity", "types", "osm", "gis", "arkh_admin", "osm_urls", "gis_urls",
                   "yandex_region", "yandex_contractor_rows"]


def source_links(ids: str) -> tuple[list[str], list[str]]:
    """`matched_source_ids` ("osm:node:1; 2gis:7000…") as (OSM, 2GIS) URLs."""
    osm, gis = [], []
    for tok in str(ids).split(";"):
        parts = tok.strip().split(":")
        if parts[0] == "osm" and len(parts) == 3:
            osm.append(f"https://www.openstreetmap.org/{parts[1]}/{parts[2]}")
        elif parts[0] == "2gis" and len(parts) == 2:
            gis.append(f"https://2gis.ru/firm/{parts[1]}")
    return osm, gis


def read_rows(path: Path) -> pd.DataFrame:
    """The `data` sheet as read, plus the object each row belongs to.

    The `quality` sheet carries the source ids (`matched_source_ids`) but in a
    *different row order* -- joined by position its `providers` disagree with
    `data`'s OSM/GIS on most rows.  `original_data` is row-aligned with `data`
    and shares five columns with `quality` that are unique per row in every
    file, so the ids travel through it by key; the join is checked to be
    one-to-one and to agree with OSM/GIS on every row, or the load fails.
    """
    df = pd.read_excel(path, sheet_name=DATA_SHEET)
    orig = pd.read_excel(path, sheet_name="original_data")
    qual = pd.read_excel(path, sheet_name="quality")
    key = ["type_orig", "name_orig", "adress_orig", "latitude", "longitude"]
    if len(orig) != len(df) or orig.duplicated(key).any() or qual.duplicated(key).any():
        raise RuntimeError(f"{path.name}: original_data/quality cannot be keyed to data")
    ids = orig[key].merge(qual[key + ["providers", "matched_source_ids"]], on=key,
                          how="left", validate="1:1")
    prov = ids["providers"].fillna("")
    if not ((prov.str.contains("osm") == df["OSM"].eq(1))
            & (prov.str.contains("2gis") == df["GIS"].eq(1))).all():
        raise RuntimeError(f"{path.name}: quality providers disagree with data OSM/GIS")
    df["_ids"] = ids["matched_source_ids"].fillna("").to_numpy()
    df["_name"] = df["name"].fillna("").astype(str).str.strip()
    df["_addr"] = df["address"].fillna("").astype(str).str.strip()
    key = entities.entity_key(df["city"], df["_name"], df["latitude"], df["longitude"])
    df["entity"] = entities.entity_ids(key)
    df["site_key"] = key
    return df


def _links(s: pd.Series, which: int) -> str:
    return " ".join(dict.fromkeys(u for ids in s for u in source_links(ids)[which]))


def _contractor_rows(g: pd.core.groupby.DataFrameGroupBy) -> pd.Series:
    """Per object, the rows where the contractor's Yandex status is «Найдено».

    JSON list of `[names, address, url]`, in row order; `names` is the
    contractor's `Яндекс: название` (every name the card list showed, "; "
    -separated), address and url "" where not a string.  The yandex check
    re-judges it by name; nothing is decided here.
    """
    def pack(d: pd.DataFrame) -> str:
        found = d[d["Яндекс: статус"].eq(STATUS_FOUND)]
        return json.dumps([[str(r["Яндекс: название"]),
                            r["Яндекс: адрес"] if isinstance(r["Яндекс: адрес"], str) else "",
                            r["Яндекс: ссылка"] if isinstance(r["Яндекс: ссылка"], str) else ""]
                           for _, r in found.iterrows()], ensure_ascii=False)
    return pd.Series({k: pack(d) for k, d in g}, name="yandex_contractor_rows")


def to_objects(df: pd.DataFrame, delivery: str, yandex_region: str,
               category: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """(ObjectTable, RowMap) from the rows `read_rows` produced.

    Catering has one row per object, flags OR-ed over the rows that repeat it; the
    contractor's flags disagree inside an object in a handful of cases and any
    row's flag counts for the object. Alcohol and tobacco preserve source rows
    so their trade and evidence types remain distinct, as in the v4 CSVs.
    """
    risk_layer = category in {"alcohol", "tobacco"}
    if risk_layer:
        # A risk workbook is a tagged layer, like the v4 CSVs: one source row
        # is one licence-join record. Grouping by site would merge distinct
        # trade/evidence types (for example a shop and its on-trade bar).
        from infracheck.inputs.v4_csv import (
            A_BAR,
            A_CATERING,
            A_SHOP,
            A_SPECIALISED,
            INFERRED_TYPES,
            ON_TRADE_TYPES,
            RISK_ALCOHOL,
            RISK_TOBACCO,
            T_HOOKAH,
            T_POSSIBLE,
            T_SPECIALISED,
            T_VAPE,
            TYPE_EN,
        )

        types = df["type"].fillna("").astype(str)
        unknown = sorted(set(types) - set(TYPE_EN))
        if unknown:
            raise ValueError(f"{delivery}: unknown {category} type(s): {unknown}")
        expected_risk = RISK_ALCOHOL if category == "alcohol" else RISK_TOBACCO
        # These vocabularies are disjoint by risk; reject a mislabeled workbook.
        if category == "alcohol":
            category_types = {A_SHOP, A_CATERING, A_SPECIALISED, A_BAR}
        else:
            category_types = {T_POSSIBLE, T_SPECIALISED, T_VAPE, T_HOOKAH}
        wrong = sorted(set(types) - category_types)
        if wrong:
            raise ValueError(f"{delivery}: {category} workbook contains other risk type(s): {wrong}")
        df = df.copy()
        df["risk"] = expected_risk
        df["trade"] = np.where(types.isin(ON_TRADE_TYPES) | types.eq(T_HOOKAH), "on-trade", "off-trade")
        df["evidence"] = np.where(types.isin(INFERRED_TYPES), "Inferred", "Direct")
        df["brand_learn"] = category == "alcohol"
        df["entity"] = np.arange(len(df), dtype="int64")
    arkh = (pd.to_numeric(df["Arkh_admin"], errors="coerce") if "Arkh_admin" in df
            else pd.Series(float("nan"), index=df.index)).fillna(0).eq(1)
    df = df.assign(_arkh=arkh)
    g = df.groupby("entity", sort=True)
    ob = pd.DataFrame({
        "delivery": delivery, "city": g["city"].first(), "name": g["_name"].first(),
        "address": g["_addr"].first(), "lat": g["latitude"].first(),
        "lon": g["longitude"].first(), "site_key": g["site_key"].first(),
        "category": category, "n_rows": g.size(),
        "entity": g["entity"].first(),
        "types": g["type"].agg(lambda s: "; ".join(sorted(set(map(str, s))))),
        "osm": g["OSM"].max().fillna(0).astype(bool),
        "gis": g["GIS"].max().fillna(0).astype(bool),
        "arkh_admin": g["_arkh"].any(),
        "osm_urls": g["_ids"].agg(lambda s: _links(s, 0)),
        "gis_urls": g["_ids"].agg(lambda s: _links(s, 1)),
        "yandex_region": yandex_region,
        "yandex_contractor_rows": _contractor_rows(g),
    })
    if risk_layer:
        from infracheck.core.names import is_generic_directory

        ob["generic"] = [is_generic_directory(n) for n in ob["name"]]
        ob["risk"] = expected_risk
        ob["trade"] = df.groupby("entity", sort=True)["trade"].first()
        ob["evidence"] = df.groupby("entity", sort=True)["evidence"].first()
        ob["brand_learn"] = df.groupby("entity", sort=True)["brand_learn"].first()
    else:
        ob["generic"] = [is_generic(n) for n in ob["name"]]
    ob.index = pd.Index([entities.object_id(delivery, e) for e in ob["entity"]], name="object_id")
    cols = list(objects.COLUMNS) + ADAPTER_COLUMNS
    if risk_layer:
        cols += ["risk", "trade", "evidence", "brand_learn"]
    ob = ob[cols].astype({c: t for c, t in objects.COLUMNS.items() if c != "category"} | {"category": "string"})
    rowmap = pd.DataFrame({"object_id": [entities.object_id(delivery, e) for e in df["entity"]],
                           "entity": df["entity"].to_numpy()},
                          index=pd.RangeIndex(len(df), name="row"))
    objects.validate(ob)
    return ob, rowmap


def load(path: Path | str, delivery: str, yandex_region: str,
         category: str = "") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read a final-delivery workbook -> (ObjectTable, RowMap, raw `data` rows).

    `yandex_region` is the path segment of Yandex Maps for the object's city
    (``20/arkhangelsk``); `category` selects catering's grouped objects or
    per-row risk records. The raw frame is `data` plus `_ids`, `_name`, `_addr`,
    `entity`, `site_key`, row-aligned with the RowMap.
    """
    raw = read_rows(Path(path))
    ob, rowmap = to_objects(raw, delivery, yandex_region, category)
    if category in {"alcohol", "tobacco"}:
        # Risk-layer records are one-per-row; keep the raw frame's entity in
        # sync with the ObjectTable/RowMap for fields later broadcast to XLSX.
        raw["entity"] = rowmap["entity"].to_numpy()
    return ob, rowmap, raw
