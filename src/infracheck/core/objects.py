"""`ObjectTable` -- what every check receives: one row per object.

An input adapter (`infracheck.inputs.*`) turns a delivery file into it; from
there on nothing downstream knows the delivery's format.  A *row* of a
delivery is not an object: the final export repeats one sign at one point
under every type it might be (Arkhangelsk catering: 2,500 rows, 1,141
objects), so the adapter groups rows into objects and keeps the mapping
(`RowMap`: row position -> object_id) for writing verdicts back.

Index: `object_id` (string, unique within one delivery).
"""
from __future__ import annotations

import pandas as pd

COLUMNS = {
    "delivery": "string",   # delivery id from config/deliveries.tsv
    "city": "string",       # "Архангельск", "Северодвинск", ...
    "name": "string",       # as written in the export
    "address": "string",    # street, house -- no city prefix; may be ""
    "lat": "float64",
    "lon": "float64",
    "site_key": "string",   # stable key of the object (cache key for Yandex)
    "category": "string",   # the export's own class, not a verdict
    "generic": "bool",      # fascia names a trade, not a business
    "n_rows": "int64",      # delivery rows that repeat this object
}
# Adapter-specific columns may follow (the final XLSX adapter keeps the
# contractor's provider flags and Yandex columns for the checks that read them).


def validate(df: pd.DataFrame) -> None:
    """Raise ValueError if `df` breaks the ObjectTable contract."""
    missing = [c for c in COLUMNS if c not in df]
    if missing:
        raise ValueError(f"ObjectTable lacks columns {missing}")
    if df.index.name != "object_id" or not df.index.is_unique:
        raise ValueError("ObjectTable must be indexed by a unique object_id")
    if df[["lat", "lon"]].isna().any().any():
        raise ValueError("ObjectTable rows without a coordinate")
