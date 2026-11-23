"""A state register as flagged in the export itself (e.g. `Росздравнадзор`).

The medical workbooks of the final delivery carry a `Росздравнадзор` column:
1 when the contractor found the organisation's name among the licensees of
Roszdravnadzor's register at the object's address (sheet `registry_source`:
"хотя бы одно название строки совпало"), 0 when it did not.  It is a name
match, not an address-only hit, so it is read as a strong, current record --
the same reading `arkh_admin` gives the administration's flag.  What the flag
does not say (that the licence is still in force) is written into the legend
by the category, not decided here.

The adapter (`inputs/final_xlsx.py`) turns the sheet column into the object
column `register` (any row of the object flagged); a delivery without that
column has no `register` column and yields no evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from infracheck.core import evidence


@dataclass(frozen=True)
class Policy:
    column: str = "register"
    source: str = "register"
    label: str = "Реестр"
    text: str = "в реестре (отметка выгрузки)"


DEFAULT = Policy()


def run(objects: pd.DataFrame, policy: Policy = DEFAULT) -> pd.DataFrame:
    """One strong/current row per object whose `policy.column` is true."""
    if policy.column not in objects:
        return evidence.empty()
    hit = objects.index[objects[policy.column].fillna(False).astype(bool)]
    if not len(hit):
        return evidence.empty()
    return evidence.make([{"object_id": oid, "check": "register_flag", "source": policy.source,
                           "identity": "strong", "record_state": "current",
                           "label": policy.label, "text": policy.text} for oid in hit])


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """`register_flag` (bool), indexed by object_id."""
    ids = index if index is not None else pd.Index(ev["object_id"].unique(), name="object_id")
    out = pd.DataFrame(index=ids)
    out.index.name = "object_id"
    out["register_flag"] = out.index.isin(ev.loc[ev["check"] == "register_flag", "object_id"])
    return out
