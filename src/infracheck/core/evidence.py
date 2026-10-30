"""`Evidence` -- the one table every check writes and every category reads.

One row per (object, source finding).  A check that found nothing for an
object may still write a row (`identity = none`) when there is something worth
saying ("searched, not found"); an object with no row at all for a check means
the check had nothing to say about it.

Three axes, kept apart on purpose (see docs/ARCHITECTURE.md, «Три оси»):

* `identity`      -- is the source record *this* object?  strong / weak / none.
                     Weak means the record speaks about the place (the door,
                     the building, the address), not about the object.
* `record_state`  -- what the record says about itself: current, stale (not
                     refreshed for years), closed (the source says it closed),
                     inactive (a register entry no longer in force), or
                     unknown (no record).
* `kind_text`     -- the source's own description of the object's kind
                     (a rubric, a category, a licence activity), raw, with
                     `kind_scheme` naming the vocabulary.  Checks never decide
                     whether that kind is "catering" -- the category does.

`source` is the organisation behind the finding (the unit of independence),
`check` the module that produced the row, `origin` the upstream dataset when
the source is an aggregator (Overture: meta / alltheplaces / foursquare).
`label` and `text` are the Russian clause the category places into
«Основания» -- before « || » when the row is counted, after it otherwise.
Checks are category-agnostic, the clause is not a verdict.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

import pandas as pd

IDENTITY = ("strong", "weak", "none")
RECORD_STATE = ("current", "stale", "closed", "inactive", "unknown")

COLUMNS = {
    "object_id": "string",     # ObjectTable index
    "check": "string",         # producing module: providers, relink, yandex_contractor, yandex, ...
    "source": "string",        # unit of independence: osm, 2gis, yandex, overture, fsq, arkh_admin, egais, export
    "origin": "string",        # upstream dataset of an aggregator record, "" otherwise
    "identity": "string",      # IDENTITY
    "record_state": "string",  # RECORD_STATE
    "kind_text": "string",     # source's own kind description, raw
    "kind_scheme": "string",   # vocabulary of kind_text: ru_text, yandex_rubric, fsq_category, overture_category, egais_activity
    "matched_name": "string",
    "matched_address": "string",
    "distance_m": "Float64",
    "name_score": "Float64",   # 0-100, the check's own name rule
    "ref": "string",           # URL / record id / licence number
    "label": "string",         # display label of the clause, e.g. "OSM", "Яндекс (подрядчик)"
    "text": "string",          # clause body for «Основания»
}

# Extra, check-specific columns are allowed after these; they travel through to
# the category untouched and are prefixed with the check name (`yandex_nearby`).


def empty() -> pd.DataFrame:
    """A zero-row Evidence frame with the canonical columns and dtypes."""
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in COLUMNS.items()})


def make(rows: Iterable[Mapping] | pd.DataFrame) -> pd.DataFrame:
    """Build an Evidence frame, filling defaults and checking the vocabulary.

    Missing canonical columns are filled ("" for strings, NA for numbers);
    extra columns are kept after the canonical ones.  Raises ValueError on an
    `identity` / `record_state` outside the vocabulary or an empty object_id.
    """
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    for c, t in COLUMNS.items():
        if c not in df:
            df[c] = pd.NA if t == "Float64" else ""
    extra = [c for c in df.columns if c not in COLUMNS]
    df = df[list(COLUMNS) + extra]
    for c, t in COLUMNS.items():
        df[c] = df[c].fillna("").astype(t) if t == "string" else df[c].astype(t)
    validate(df)
    return df.reset_index(drop=True)


def validate(df: pd.DataFrame) -> None:
    """Raise ValueError if `df` breaks the Evidence contract."""
    missing = [c for c in COLUMNS if c not in df]
    if missing:
        raise ValueError(f"Evidence lacks columns {missing}")
    bad = set(df["identity"]) - set(IDENTITY)
    if bad:
        raise ValueError(f"identity outside {IDENTITY}: {sorted(bad)}")
    bad = set(df["record_state"]) - set(RECORD_STATE)
    if bad:
        raise ValueError(f"record_state outside {RECORD_STATE}: {sorted(bad)}")
    if (df["object_id"] == "").any():
        raise ValueError("Evidence rows with an empty object_id")
