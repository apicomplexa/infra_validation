"""Parity of the final-delivery input and the directory checks with the archive.

Loads the three final XLSX, runs providers / arkh_admin / yandex against the
archived Yandex cache, rebuilds the archive's wide columns and compares them
with `resources/golden/final_confirm/final_confirm_objects.csv` -- as strings,
both sides written to CSV and read back the same way, so that NaN vs "" cannot
hide a difference.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from infracheck.checks import arkh_admin, providers, yandex
from infracheck.inputs import final_xlsx

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "resources" / "cache" / "yandex" / "final_yandex_cache.jsonl"

COLUMNS = ["city", "name", "address", "lat", "lon", "site_key", "n_rows", "types",
           "osm", "gis", "arkh_admin", "ya_contractor", "ya_contractor_raw",
           "ya_c_name", "ya_c_addr", "ya_c_url", "osm_urls", "gis_urls", "generic",
           "osm_link", "gis_link", "link_note", "osm_link_from", "gis_link_from",
           "ya_status", "ya_name", "ya_address", "ya_url", "ya_distance_m",
           "ya_nearby", "ya_search_url", "ya_checked"]


def as_text(df: pd.DataFrame) -> pd.DataFrame:
    """Round-trip through CSV, the way the archive wrote its golden file."""
    buf = io.StringIO()
    df.to_csv(buf, sep=";", index=False)
    buf.seek(0)
    return pd.read_csv(buf, sep=";", dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def built(golden):
    deliveries = pd.read_csv(ROOT / "config" / "deliveries.tsv", sep="\t", dtype=str)
    deliveries = deliveries[deliveries["format"] == "final_xlsx"]
    cache = yandex.load_cache(CACHE)
    parts = []
    for _, d in deliveries.iterrows():
        ob, rowmap, raw = final_xlsx.load(ROOT / d["path"], d["delivery"], d["yandex_region"], d["category"])
        ev = pd.concat([providers.run(ob), arkh_admin.run(ob), yandex.run(ob, cache=cache)], ignore_index=True)
        w = pd.concat([providers.wide(ev, ob.index), arkh_admin.wide(ev, ob.index),
                       yandex.wide(ev, ob.index)], axis=1)
        out = ob[["city", "name", "address", "lat", "lon", "site_key", "n_rows", "types"]].join(w)
        out["generic"] = ob["generic"]
        parts.append((Path(d["path"]).name, ob, rowmap, raw, out[COLUMNS], cache))
    return parts


@pytest.mark.golden
def test_row_map_covers_every_row(built):
    for _, ob, rowmap, raw, _, _ in built:
        assert len(rowmap) == len(raw)
        assert set(rowmap["object_id"]) == set(ob.index)
        assert (rowmap["object_id"].value_counts().reindex(ob.index).to_numpy() == ob["n_rows"].to_numpy()).all()


@pytest.mark.golden
def test_search_url_reproduces_cached_urls(built):
    for _, ob, _, _, _, cache in built:
        for _, r in ob.iterrows():
            rec = cache.get(r["site_key"])
            if rec:
                assert yandex.search_url(r["name"], r["address"], r["lat"], r["lon"], r["city"],
                                         r["yandex_region"]) == rec["search_url"]


@pytest.mark.golden
def test_parity_with_final_confirm_objects(built, golden):
    gold = pd.read_csv(golden / "final_confirm" / "final_confirm_objects.csv", sep=";",
                       encoding="utf-8-sig", dtype=str, keep_default_na=False)
    total, bad = 0, {}
    for fname, _, _, _, out, _ in built:
        g = gold[gold["file"] == fname].reset_index(drop=True)
        mine = as_text(out.reset_index(drop=True))
        assert len(g) == len(mine), fname
        total += len(g)
        for c in COLUMNS:
            n = int((g[c] != mine[c]).sum())
            if n:
                bad[c] = bad.get(c, 0) + n
    assert total == 1688
    assert not bad, f"columns with mismatches: {bad}"
