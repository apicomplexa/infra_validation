"""Rows of a delivery -> objects: the grouping rule, independent of the file format.

A *row* of the final export is not an object: one sign at one point is repeated
under every type it might be (кафе / ресторан / столовая / кулинария).  Two rows
are the same object when they share city, written name and coordinate (to six
decimals).  The key doubles as `site_key`, the stable key of the object and the
cache key of the Yandex check.

Ported from `arkhinfra.final_confirm.load` / `objects` (archive tag
`archive-2026-10-30`).
"""
from __future__ import annotations

import pandas as pd


def entity_key(city: pd.Series, name: pd.Series, lat: pd.Series, lon: pd.Series) -> pd.Series:
    """`city|name|lat6|lon6` -- one string per row, equal for rows of one object.

    `name` is expected already stripped.  Coordinates are rounded to six
    decimals *before* being written as text, as the archive did.
    """
    return (city.astype(str) + "|" + name + "|"
            + lat.round(6).astype(str) + "|" + lon.round(6).astype(str))


def entity_ids(keys: pd.Series) -> pd.Series:
    """Integer entity per row, numbered in order of first appearance."""
    return pd.Series(pd.factorize(keys)[0], index=keys.index, name="entity")


def object_id(delivery: str, entity: int) -> str:
    """Object id: unique across deliveries, so tables of several can be stacked."""
    return f"{delivery}:{entity}"


def first_nonempty(s: pd.Series) -> str:
    """First non-empty string of a group, in row order; "" if none."""
    return next((v for v in s if isinstance(v, str) and v and v != "nan"), "")
