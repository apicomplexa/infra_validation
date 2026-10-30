"""What the open place directories (Overture, Foursquare) share.

Both checks look for the nearest alike-named record around an object and let
one record confirm one site only.  Ported from the archive
(`arkhinfra.final_confirm.overture` / `foursquare` / `exclusive`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core import geo

# Same acceptance radii as our Yandex check: a generic fascia ("Продукты",
# "Кофейный автомат") is accepted only at arm's length, one building's depth.
NEAR_M = 150.0
GENERIC_M = 60.0
RELINK_M = 50.0


def radius(generic: bool, near_m: float = NEAR_M, generic_m: float = GENERIC_M) -> float:
    return generic_m if generic else near_m


def candidates(lat: float, lon: float, generic: bool, places: pd.DataFrame, *,
               near_m: float = NEAR_M, generic_m: float = GENERIC_M,
               allowed: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Row positions of `places` within the object's radius, nearest first.

    Returns (positions, distances of all places in metres).  `allowed` is an
    optional boolean mask over `places` (e.g. confidence floor).
    """
    d = geo.dist_to(lat, lon, places["lat"].to_numpy(float), places["lon"].to_numpy(float))
    mask = d <= radius(generic, near_m, generic_m)
    if allowed is not None:
        mask = mask & allowed
    pos = np.where(mask)[0]
    return pos[np.argsort(d[mask])], d


def exclusive(evidence: pd.DataFrame, objects: pd.DataFrame, relink_m: float = RELINK_M, *,
              note_col: str) -> pd.DataFrame:
    """One place record confirms one site, not every alike-named object near it.

    A record matched by several objects goes to the nearest of them and to the
    objects within `relink_m` of that one -- the contractor's own unmerged
    duplicates («Библио-Кофе» / «Библио-кофе, кофейня», one door).  The rest
    lose it: Hindi on Троицкий 61 and 67 are two restaurants, one FSQ point.

    `evidence` holds the found records (one row per object, `ref` = record id,
    `distance_m`); losers get `identity = weak` and the note in `note_col`.
    The scope is the `objects` table passed in: the archive ran it once per
    delivery file, i.e. per city.
    """
    ev = evidence.copy()
    if ev.empty:
        return ev
    found = ev[ev["ref"].astype(str).ne("")]
    for _, g in found[found["ref"].duplicated(keep=False)].groupby("ref"):
        w = g["distance_m"].astype(float).idxmin()
        ob = objects.loc[g["object_id"]]
        d = geo.dist_matrix(ob["lat"].to_numpy(float), ob["lon"].to_numpy(float))[g.index.get_loc(w)]
        win = objects.loc[ev.at[w, "object_id"]]
        for i, di in zip(g.index, d, strict=True):
            if di <= relink_m:
                continue
            ev.at[i, "identity"] = "weak"
            ev.at[i, note_col] = (f"запись ближе к объекту «{win['name']}» ({win['address']}), "
                                  f"{di:.0f} м отсюда")
    return ev
