"""The export's own description of an object's kind, from its name.

2GIS writes a fascia and then its trade after a comma -- «Штаб, ресто-кафе»,
«Tupperware, дистрибьюторный центр».  That descriptor is a vote about the
kind of the object (what *sort* of place it is); a name with no comma is read
whole.  The vote is raw text: this check never decides whether the kind is
catering -- the category does -- and it says nothing about existence (the
category excludes `source = "export"` from the count).

Ported from the first vote of `arkhinfra.final_confirm.profile`.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from infracheck.core import evidence


@dataclass(frozen=True)
class Policy:
    descriptor_label: str = "2GIS, описание в названии"
    bare_label: str = "название"
    separator: str = ","


DEFAULT = Policy()


def _vote(name: str, policy: Policy) -> tuple[str, str]:
    """(label, kind_text) of one name."""
    if policy.separator in name:
        return policy.descriptor_label, name.rsplit(policy.separator, 1)[1].strip()
    return policy.bare_label, name


def run(objects: pd.DataFrame, policy: Policy = DEFAULT) -> pd.DataFrame:
    """One kind vote per object: identity strong, record current, scheme ru_text."""
    rows = []
    for oid, name in objects["name"].items():
        label, kind = _vote(str(name), policy)
        rows.append({"object_id": oid, "check": "export_name", "source": "export",
                     "identity": "strong", "record_state": "current",
                     "kind_text": kind, "kind_scheme": "ru_text", "label": label, "text": ""})
    return evidence.make(rows) if rows else evidence.empty()


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """`export_label` and `export_kind`, indexed by object_id."""
    e = ev[ev["check"] == "export_name"].set_index("object_id")
    out = pd.DataFrame({"export_label": e["label"], "export_kind": e["kind_text"]})
    return out if index is None else out.reindex(index).fillna("")
