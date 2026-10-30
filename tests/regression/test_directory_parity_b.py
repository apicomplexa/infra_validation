"""Parity of the Overture and Foursquare checks with the archive's final_confirm output."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from infracheck.checks import foursquare, overture

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

OV_COLS = ["ov_match", "ov_name", "ov_category", "ov_dataset", "ov_confidence",
           "ov_distance_m", "ov_id", "ov_note"]
FSQ_COLS = ["fsq_match", "fsq_name", "fsq_category", "fsq_refreshed",
            "fsq_distance_m", "fsq_id", "fsq_note"]


def _as_text(df: pd.DataFrame) -> pd.DataFrame:
    """Round-trip through CSV so both sides are strings formatted the same way."""
    buf = io.StringIO()
    df.to_csv(buf, sep=";", index=False)
    buf.seek(0)
    return pd.read_csv(buf, sep=";", dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def golden_objects(golden) -> pd.DataFrame:
    g = pd.read_csv(golden / "final_confirm" / "final_confirm_objects.csv", sep=";",
                    encoding="utf-8-sig", dtype=str, keep_default_na=False)
    assert len(g) == 1688
    g["object_id"] = g["file"] + "#" + g.groupby("file").cumcount().astype(str)
    return g.set_index("object_id")


def _objects(g: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "delivery": g["file"], "city": g["city"], "name": g["name"], "address": g["address"],
        "lat": g["lat"].astype(float), "lon": g["lon"].astype(float), "site_key": g["site_key"],
        "category": "", "generic": g["generic"].eq("True"), "n_rows": g["n_rows"].astype(int),
    }, index=g.index)


def _parity(golden_objects, mod, cols, snapshot_dir):
    places = mod.load(ROOT / "resources" / snapshot_dir)
    assert places is not None
    parts = []
    for _, g in golden_objects.groupby("file", sort=False):    # the archive's scope: one file
        ob = _objects(g)
        parts.append(mod.wide(mod.run(ob, places), index=ob.index))
    got = pd.concat(parts)
    assert (got.index == golden_objects.index).all()
    want = golden_objects[cols]
    return want, _as_text(got[cols].reset_index(drop=True))


@pytest.mark.golden
@pytest.mark.parametrize("name,mod,cols,snap", [
    ("overture", overture, OV_COLS, "overture"),
    ("foursquare", foursquare, FSQ_COLS, "foursquare"),
])
def test_directory_parity(golden_objects, name, mod, cols, snap):
    want, got = _parity(golden_objects, mod, cols, snap)
    want = want.reset_index(drop=True)
    diff = {c: int((want[c] != got[c]).sum()) for c in cols}
    assert not any(diff.values()), f"{name} mismatches per column: {diff}"
