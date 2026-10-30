"""Overture Places as a source of existence (`fetch.overture` downloads it).

The one open POI layer independent of all three directories the export already
uses: its places theme has no OSM, only Meta (Facebook pages, unmaintained from
Russia since 2022 -- the weaker kind), Foursquare and AllThePlaces (chains).
Every place is matched, not only catering: existence does not need Overture to
agree on the kind.  Whether a place built from Foursquare alone counts is a
category rule (`origin` carries the dataset).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..core import evidence
from ..core.names import similar
from . import _directory as D


@dataclass(frozen=True)
class Policy:
    # Overture's own `confidence` below this is its own "probably not there".
    conf_min: float = 0.5
    # Stricter than the Yandex name floor: AllThePlaces is chains, so a
    # same-brand branch is often the nearest place, and at 72 «Coffee Lab» took
    # «Coffee Like» 120 m away and «Stare Mesto» the hostel «Старое Место».  85
    # drops those and two true pairs («Америго» / «Боулинг-центр «Америго»»,
    # «Эдисон» / «Бар Edison»).
    name_min: float = 85.0
    near_m: float = D.NEAR_M
    generic_m: float = D.GENERIC_M
    relink_m: float = D.RELINK_M


def load(path: Path | str | None = None) -> pd.DataFrame | None:
    """The explicit snapshot, or the newest `overture_places_*.parquet` in a directory.

    None when absent (the source is then skipped).
    """
    p = Path(path) if path else None
    files = sorted(p.glob("overture_places_*.parquet")) if p is not None and p.is_dir() else [p]
    if not files or files[-1] is None or not files[-1].exists():
        return None
    import duckdb  # the env reads parquet through duckdb as well
    pl = duckdb.sql(f"SELECT * FROM '{files[-1]}'").df()
    pl["name"] = pl["name"].fillna("").astype(str)
    return pl[pl["name"].ne("")].reset_index(drop=True)


def _counted(name, category, dist, dataset, conf, rid) -> str:
    return f"«{name}» ({category}), {dist} м, источник {dataset}, уверенность {conf}, id {rid}"


def run(objects: pd.DataFrame, places: pd.DataFrame | None, policy: Policy | None = None) -> pd.DataFrame:
    """Nearest alike-named Overture place for every object, if any; exclusivity applied."""
    policy = policy or Policy()
    rows = []
    if places is not None and not places.empty:
        conf = places["confidence"].fillna(0).to_numpy(float)
        for oid, r in objects.iterrows():
            pos, d = D.candidates(r["lat"], r["lon"], bool(r["generic"]), places,
                                  near_m=policy.near_m, generic_m=policy.generic_m,
                                  allowed=conf >= policy.conf_min)
            for j in pos:
                if similar(r["name"], places.at[j, "name"]) >= policy.name_min:
                    rows.append({
                        "object_id": oid, "check": "overture", "source": "overture",
                        "origin": places.at[j, "datasets"] or places.at[j, "dataset"],
                        "identity": "strong", "record_state": "current",
                        "kind_text": places.at[j, "category"] or "", "kind_scheme": "overture_category",
                        "matched_name": places.at[j, "name"],
                        "matched_address": places.at[j, "address"] if "address" in places else "",
                        "distance_m": round(float(d[j]), 1), "name_score": np.nan,
                        "ref": places.at[j, "id"], "label": "Overture",
                        "overture_confidence": round(float(conf[j]), 2), "overture_note": "",
                    })
                    break
    if not rows:
        return _empty()
    ev = D.exclusive(pd.DataFrame(rows), objects, policy.relink_m, note_col="overture_note")
    counted = ev["identity"].eq("strong")
    ev["text"] = [
        _counted(r.matched_name, r.kind_text or "", r.distance_m, r.origin, r.overture_confidence, r.ref)
        if c else f"«{r.matched_name}» — {r.overture_note}"
        for r, c in zip(ev.itertuples(), counted, strict=True)]
    return evidence.make(ev)


def _empty() -> pd.DataFrame:
    return evidence.make(evidence.empty().assign(overture_confidence=pd.Series(dtype="float64"),
                                                 overture_note=pd.Series(dtype="string")))


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """Archive-shaped columns ov_*, one row per object (reindexed to `index`)."""
    ev = ev.set_index("object_id")
    w = pd.DataFrame({
        "ov_match": ev["identity"].eq("strong") & ev["record_state"].eq("current"),
        "ov_name": ev["matched_name"].astype(object), "ov_category": ev["kind_text"].astype(object),
        "ov_dataset": ev["origin"].astype(object),
        "ov_confidence": ev["overture_confidence"].astype(float),
        "ov_distance_m": ev["distance_m"].astype(float),
        "ov_id": ev["ref"].astype(object), "ov_note": ev["overture_note"].astype(object),
    })
    w.index.name = "object_id"
    if index is not None:
        w = w.reindex(index)
    w["ov_match"] = w["ov_match"].fillna(False).astype(bool)
    for c in ("ov_name", "ov_category", "ov_dataset", "ov_id", "ov_note"):
        w[c] = w[c].fillna("")
    return w
