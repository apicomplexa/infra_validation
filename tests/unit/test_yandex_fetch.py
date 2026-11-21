"""Repeated risk-layer rows share a query; different names still need separate answers."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from infracheck.checks import yandex


@pytest.fixture
def objects():
    return pd.DataFrame([
        {"name": name, "address": "улица Мира, 1", "city": "Новодвинск",
         "lat": 64.41, "lon": 40.82, "site_key": "same-site"}
        for name in ["Первый", "Первый", "Второй"]
    ])


def test_fetch_deduplicates_queries_and_resumes_from_cache(objects, monkeypatch, tmp_path):
    calls = []

    def read(name, address, lat, lon, **kwargs):
        calls.append(name)
        return {"search_url": yandex.search_url(name, address, lat, lon, kwargs["city"]),
                "candidates": [], "note": ""}

    monkeypatch.setattr(yandex, "read", read)
    cache_path = tmp_path / "cache.jsonl"
    cache = {}
    # The page limit counts unique queries, not duplicate source rows.
    assert yandex.fetch_missing(objects, cache, cache_path, limit=1) == 1
    assert calls == ["Первый"]
    assert yandex.fetch_missing(objects, yandex.load_cache(cache_path), cache_path) == 1
    assert calls == ["Первый", "Второй"]
    assert len(yandex.load_cache(cache_path)) == 2
    assert yandex.fetch_missing(objects, yandex.load_cache(cache_path), cache_path) == 0
    assert calls == ["Первый", "Второй"]


def test_failed_query_is_attempted_once_and_can_retry(objects, monkeypatch, tmp_path):
    calls = []

    def read(*args, **kwargs):
        calls.append(args[0])
        return {"candidates": [], "note": "temporary failure"}

    monkeypatch.setattr(yandex, "read", read)
    cache_path = tmp_path / "cache.jsonl"
    cache = {}
    assert yandex.fetch_missing(objects, cache, cache_path) == 0
    assert calls == ["Первый", "Второй"]
    assert cache == {}
    assert not cache_path.exists()
    assert yandex.fetch_missing(objects, cache, cache_path, limit=1) == 0
    assert calls == ["Первый", "Второй", "Первый"]


def test_cli_reports_failure_after_finishing_queue(objects, monkeypatch, tmp_path):
    calls = []

    def read(name, address, lat, lon, **kwargs):
        calls.append(name)
        if name == "Первый":
            return {"candidates": [], "note": "temporary failure"}
        return {"search_url": yandex.search_url(name, address, lat, lon, kwargs["city"]),
                "candidates": [], "note": ""}

    monkeypatch.setattr(yandex, "read", read)
    monkeypatch.setattr(pd, "read_parquet", lambda _: objects)
    cache_path = tmp_path / "cache.jsonl"
    assert yandex.main(["fetch", "--objects", "unused", "--cache", str(cache_path)]) == 1
    assert calls == ["Первый", "Второй"]
    assert len(yandex.load_cache(cache_path)) == 1
    monkeypatch.setattr(yandex, "read", lambda name, address, lat, lon, **kwargs: {
        "search_url": yandex.search_url(name, address, lat, lon, kwargs["city"]),
        "candidates": [], "note": "",
    })
    assert yandex.main(["fetch", "--objects", "unused", "--cache", str(cache_path)]) == 0
    assert len(yandex.load_cache(cache_path)) == 2


@pytest.mark.parametrize("contractor_name", [None, "Первый", "Чужая организация"])
def test_wide_handles_no_cached_answers(objects, contractor_name):
    ob = objects.iloc[:1].copy()
    ob.index = pd.Index(["a"], name="object_id")
    ob["yandex_contractor_rows"] = json.dumps(
        [[contractor_name, "Мира, 1", "https://yandex.ru/maps/org/1/"]] if contractor_name else [],
        ensure_ascii=False,
    )
    ev = yandex.run(ob, cache={})
    wide = yandex.wide(ev, ob.index)
    assert wide.index.equals(ob.index)
    assert wide.loc["a", "ya_status"] == ""
    assert wide.loc["a", "ya_search_url"] == ""
    assert wide.loc["a", "ya_distance_m"] == ""
    assert bool(wide.loc["a", "ya_contractor_raw"]) == bool(contractor_name)
    assert bool(wide.loc["a", "ya_contractor"]) == (contractor_name == "Первый")
