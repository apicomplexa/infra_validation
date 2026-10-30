"""Distances on the sphere, in metres."""
from __future__ import annotations

import numpy as np
from licmatch import EARTH_R_M, haversine_m

__all__ = ["EARTH_R_M", "dist_matrix", "dist_to", "haversine_m"]


def dist_matrix(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """All-pairs haversine distance between the points of one set."""
    la, lo = np.radians(lat), np.radians(lon)
    dla = la[:, None] - la[None, :]
    dlo = lo[:, None] - lo[None, :]
    h = np.sin(dla / 2) ** 2 + np.cos(la)[:, None] * np.cos(la)[None, :] * np.sin(dlo / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def dist_to(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Distance from one point to many (`lats`, `lons` in degrees)."""
    la, lo = np.radians(lat), np.radians(lon)
    plat, plon = np.radians(lats), np.radians(lons)
    h = np.sin((plat - la) / 2) ** 2 + np.cos(la) * np.cos(plat) * np.sin((plon - lo) / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
