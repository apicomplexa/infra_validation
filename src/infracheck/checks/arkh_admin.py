"""The city administration's register, as flagged in the export (`Arkh_admin`).

One more independent source: the export marks an object `Arkh_admin = 1` when
the Arkhangelsk administration's own register lists it.  Read as it stands.

Ported from `arkhinfra.final_confirm.decide` (the «Администрация» clause).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from infracheck.core import evidence


@dataclass(frozen=True)
class Policy:
    label: str = "Администрация"
    text: str = "в реестре (Arkh_admin = 1)"


DEFAULT = Policy()


def run(objects: pd.DataFrame, policy: Policy = DEFAULT) -> pd.DataFrame:
    """One strong/current row per object flagged `arkh_admin`."""
    hit = objects.index[objects["arkh_admin"].astype(bool)]
    if not len(hit):
        return evidence.empty()
    return evidence.make([{"object_id": oid, "check": "arkh_admin", "source": "arkh_admin",
                           "identity": "strong", "record_state": "current",
                           "label": policy.label, "text": policy.text} for oid in hit])


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """`arkh_admin` (bool), indexed by object_id."""
    ids = index if index is not None else pd.Index(ev["object_id"].unique(), name="object_id")
    out = pd.DataFrame(index=ids)
    out.index.name = "object_id"
    out["arkh_admin"] = out.index.isin(ev.loc[ev["check"] == "arkh_admin", "object_id"])
    return out
