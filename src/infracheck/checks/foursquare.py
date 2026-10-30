"""Foursquare OS Places direct (`fetch.foursquare` downloads it).

~16k places in the box against Overture's 200 Foursquare rows.  Most were last
refreshed in 2013-2016, and a 2014 record says the place existed in 2014; only
a record refreshed since `Policy.fresh` counts, older matches are shown, not
counted.  A record with `date_closed` never counts.  Name floor as for
Overture.
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
    fresh: str = "2022-01-01"
    name_min: float = 85.0
    near_m: float = D.NEAR_M
    generic_m: float = D.GENERIC_M
    relink_m: float = D.RELINK_M


def load(path: Path | str | None = None) -> pd.DataFrame | None:
    """The explicit snapshot, or the newest `fsq_places_*.parquet` in a directory; None when absent."""
    p = Path(path) if path else None
    files = sorted(p.glob("fsq_places_*.parquet")) if p is not None and p.is_dir() else [p]
    if not files or files[-1] is None or not files[-1].exists():
        return None
    import duckdb
    pl = duckdb.sql(f"SELECT * FROM '{files[-1]}'").df()
    pl["name"] = pl["name"].fillna("").astype(str)
    for c in ("date_refreshed", "date_closed", "categories"):
        pl[c] = pl[c].fillna("").astype(str)
    return pl[pl["name"].ne("")].reset_index(drop=True)


def run(objects: pd.DataFrame, places: pd.DataFrame | None, policy: Policy | None = None) -> pd.DataFrame:
    """Nearest alike-named FSQ place, preferring a fresh open one; exclusivity applied.

    A found record is reported either way: strong/current when fresh and open,
    strong/stale or strong/closed (with the reason in `foursquare_note`) when
    not, weak when another object has the record.
    """
    policy = policy or Policy()
    rows = []
    if places is not None and not places.empty:
        fresh = places["date_refreshed"].ge(policy.fresh).to_numpy()
        closed = places["date_closed"].ne("").to_numpy()
        for oid, r in objects.iterrows():
            near, d = D.candidates(r["lat"], r["lon"], bool(r["generic"]), places,
                                   near_m=policy.near_m, generic_m=policy.generic_m)
            hits = [j for j in near if similar(r["name"], places.at[j, "name"]) >= policy.name_min]
            if not hits:
                continue
            # A fresh open record wins over a nearer stale or closed one.
            best = next((j for j in hits if fresh[j] and not closed[j]), hits[0])
            ok = bool(fresh[best] and not closed[best])
            note = ("" if ok else f"закрыт с {places.at[best, 'date_closed']}" if closed[best]
                    else f"запись не обновлялась с {places.at[best, 'date_refreshed'][:4]}")
            rows.append({
                "object_id": oid, "check": "foursquare", "source": "fsq", "origin": "",
                "identity": "strong",
                "record_state": "current" if ok else "closed" if closed[best] else "stale",
                "kind_text": places.at[best, "categories"], "kind_scheme": "fsq_category",
                "matched_name": places.at[best, "name"],
                "matched_address": places.at[best, "address"] if "address" in places else "",
                "distance_m": round(float(d[best]), 1), "name_score": np.nan,
                "ref": places.at[best, "fsq_place_id"], "label": "Foursquare",
                "foursquare_refreshed": places.at[best, "date_refreshed"], "foursquare_note": note,
            })
    if not rows:
        return evidence.make(evidence.empty().assign(foursquare_refreshed=pd.Series(dtype="string"),
                                                     foursquare_note=pd.Series(dtype="string")))
    ev = D.exclusive(pd.DataFrame(rows), objects, policy.relink_m, note_col="foursquare_note")
    counted = ev["identity"].eq("strong") & ev["record_state"].eq("current")
    ev["text"] = [
        f"«{r.matched_name}» ({r.kind_text}), {r.distance_m} м, обновлено {r.foursquare_refreshed}, id {r.ref}"
        if c else f"«{r.matched_name}» {r.distance_m} м, {r.foursquare_note}"
        for r, c in zip(ev.itertuples(), counted, strict=True)]
    return evidence.make(ev)


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """Archive-shaped columns fsq_*, one row per object (reindexed to `index`)."""
    ev = ev.set_index("object_id")
    w = pd.DataFrame({
        "fsq_match": ev["identity"].eq("strong") & ev["record_state"].eq("current"),
        "fsq_name": ev["matched_name"].astype(object), "fsq_category": ev["kind_text"].astype(object),
        "fsq_refreshed": ev["foursquare_refreshed"].astype(object),
        "fsq_distance_m": ev["distance_m"].astype(float),
        "fsq_id": ev["ref"].astype(object), "fsq_note": ev["foursquare_note"].astype(object),
    })
    w.index.name = "object_id"
    if index is not None:
        w = w.reindex(index)
    w["fsq_match"] = w["fsq_match"].fillna(False).astype(bool)
    for c in ("fsq_name", "fsq_category", "fsq_refreshed", "fsq_id", "fsq_note"):
        w[c] = w[c].fillna("")
    return w
