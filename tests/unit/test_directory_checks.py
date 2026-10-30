"""Pure helpers of the directory checks: parse, judge, relink, contractor re-judge."""
from __future__ import annotations

import json

import pandas as pd

from infracheck.checks import arkh_admin, export_name, providers, yandex

CARD = """<html><link rel="canonical" href="https://yandex.ru/maps/org/kdv/186769920591/">
<meta property="og:title" content="KDV, кондитерская, ул. Дежнёвцев, 34, Исакогорский округ — Яндекс Карты">
<div data-object="search-result"><div data-id="186769920591" data-coordinates="40.58289,64.51161"></div>
<h1 itemProp="name"><span hidden></span>KDV</h1><meta itemProp="address" content="Архангельск, улица Дежнёвцев, 34">
</div></html>"""

LIST = """<div data-object="search-list-item" data-id="1" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Штаб</div>
<div class="search-business-snippet-view__address">ул. Ленина, 1</div> Больше не работает</div>
<div data-object="search-list-item" data-id="2" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Другой</div>
<div class="search-business-snippet-view__address">ул. Ленина, 2</div></div>"""


def ob(rows):
    df = pd.DataFrame(rows).set_index("object_id")
    return df


def test_parse_card_and_list():
    a = yandex.parse(CARD, "u")
    assert [(c.oid, c.name) for c in a.candidates] == [("186769920591", "KDV")]
    assert a.candidates[0].address.endswith("34") and not a.candidates[0].closed
    b = yandex.parse(LIST)
    assert [c.name for c in b.candidates] == ["Штаб", "Другой"]
    assert b.candidates[0].closed and not b.candidates[1].closed


def test_judge_statuses():
    a = yandex.parse(CARD)
    v = yandex.judge(a, "KDV, кондитерская", 64.51158, 40.582827)
    assert v["status"] == yandex.STATUS_PRESENT and v["yandex_distance_m"] < 10
    assert yandex.judge(a, "Совсем другое", 64.51158, 40.582827)["status"] == yandex.STATUS_ABSENT
    assert yandex.judge(a, "Кафе", 64.51158, 40.582827, generic=True)["status"] == yandex.STATUS_AMBIGUOUS
    closed = yandex.judge(yandex.parse(LIST), "Штаб, ресто-кафе", 64.5, 40.5)
    assert closed["status"] == yandex.STATUS_CLOSED


def test_cache_roundtrip_and_error(tmp_path):
    rec = {**yandex.parse(CARD, "u").to_dict(), "note": "", "checked": "2026-10-29", "site_key": "k"}
    p = tmp_path / "c.jsonl"
    yandex.append_cache(rec, p)
    assert yandex.load_cache(p)["u"]["checked"] == "2026-10-29"   # keyed by query
    assert yandex.verdict({"note": "rate-limited"}, "x", 0, 0)["status"] == yandex.STATUS_ERROR


def test_search_url_region():
    u = yandex.search_url("Йоко", "Северодвинск, Ленина, 1", 64.5, 39.8, "Северодвинск", "10849/severodvinsk")
    assert u.startswith("https://yandex.ru/maps/10849/severodvinsk/search/")
    assert "ll=39.800000%2C64.500000" in u


def test_rubric_stops_at_address():
    rec = {"canonical": "https://yandex.ru/maps/org/kdv/186769920591/",
           "og_title": "KDV, кондитерская, кофейня, ул. Дежнёвцев, 34 — Яндекс Карты"}
    assert yandex.rubric(rec, "https://yandex.ru/maps/org/186769920591/") == "кондитерская, кофейня"
    assert yandex.rubric(rec, "https://yandex.ru/maps/org/999999999/") == ""


def test_rejudge_contractor():
    rows = json.dumps([["Tupperware; Йоко суши", "ул. А, 1", "http://y/1"]])
    assert yandex.rejudge_contractor(rows, "Йоко суши")["ok"]
    r = yandex.rejudge_contractor(rows, "Совсем иное")
    assert r["raw"] and not r["ok"]
    assert not yandex.rejudge_contractor("[]", "Йоко суши")["raw"]


def test_yandex_run_axes():
    objs = ob([{"object_id": "d:0", "name": "KDV", "lat": 64.51158, "lon": 40.582827,
                "address": "улица Дежнёвцев, 34", "city": "Архангельск",
                "generic": False, "site_key": "k0", "yandex_contractor_rows": json.dumps([["Чужой", "", ""]])}])
    rec = {**yandex.parse(CARD, "u").to_dict(), "note": "", "checked": "2026-10-29"}
    ev = yandex.run(objs, cache={yandex.own_query(objs.iloc[0]): rec})
    assert list(ev["check"]) == ["yandex_contractor", "yandex"]
    assert list(ev["identity"]) == ["weak", "strong"]
    assert ev.loc[0, "text"] == "по адресу есть общепит, но с другим названием"
    assert ev.loc[1, "kind_text"] == "кондитерская" and ev.loc[1, "kind_scheme"] == "yandex_rubric"
    w = yandex.wide(ev, objs.index)
    assert w.loc["d:0", "ya_status"] == "present" and not w.loc["d:0", "ya_contractor"]
    assert w.loc["d:0", "ya_contractor_raw"]


def test_relink_pairs_twin_within_limit():
    objs = ob([
        {"object_id": "a", "city": "X", "name": "1234", "lat": 64.0, "lon": 40.0, "generic": False,
         "osm": True, "gis": False, "osm_urls": "u", "gis_urls": ""},
        {"object_id": "b", "city": "X", "name": "1234, кофейня", "lat": 64.0001, "lon": 40.0, "generic": False,
         "osm": True, "gis": True, "osm_urls": "", "gis_urls": "https://2gis.ru/firm/7"},
        {"object_id": "c", "city": "X", "name": "Далёкий", "lat": 64.01, "lon": 40.0, "generic": False,
         "osm": False, "gis": True, "osm_urls": "", "gis_urls": ""},
    ])
    out = providers.relink(objs)
    assert out.loc["a", "gis_link"] and not out.loc["c", "osm_link"]
    assert out.loc["a", "gis_link_from"].startswith("«1234, кофейня», 11 м, https://2gis.ru")
    ev = providers.run(objs)
    assert set(ev["check"]) == {"providers", "relink"}
    rl = ev[ev["check"] == "relink"]
    assert list(rl["object_id"]) == ["a"] and rl.iloc[0]["text"].startswith("соседняя запись «1234, кофейня»")
    w = providers.wide(ev, objs.index)
    assert w.loc["a", "link_note"] == "2GIS из записи «1234, кофейня» (11 м)"
    assert w.loc["b", "osm_urls"] == "" and w.loc["a", "osm_urls"] == "u"


def test_relink_generic_limit_is_tighter():
    objs = ob([
        {"object_id": "a", "city": "X", "name": "Столовая", "lat": 64.0, "lon": 40.0, "generic": True,
         "osm": True, "gis": False, "osm_urls": "", "gis_urls": ""},
        {"object_id": "b", "city": "X", "name": "Столовая", "lat": 64.00033, "lon": 40.0, "generic": True,
         "osm": False, "gis": True, "osm_urls": "", "gis_urls": ""},
    ])   # ~37 m apart: inside 50 m, outside 20 m
    assert not providers.relink(objs)["gis_link"].any()


def test_export_name_and_arkh_admin():
    objs = ob([{"object_id": "a", "name": "Штаб, ресто-кафе", "arkh_admin": True},
               {"object_id": "b", "name": "Coffee Like", "arkh_admin": False}])
    ev = export_name.run(objs)
    assert list(ev["label"]) == ["2GIS, описание в названии", "название"]
    assert list(ev["kind_text"]) == ["ресто-кафе", "Coffee Like"]
    assert set(ev["source"]) == {"export"} and set(ev["kind_scheme"]) == {"ru_text"}
    aa = arkh_admin.run(objs)
    assert list(aa["object_id"]) == ["a"] and aa.loc[0, "label"] == "Администрация"
    assert list(arkh_admin.wide(aa, objs.index)["arkh_admin"]) == [True, False]


def test_cache_answers_the_query_not_the_door():
    # Two records at one coordinate (an alcohol and a tobacco shop at one door
    # share a v4 site_key): the cached answer to one name is not the other's.
    base = {"lat": 64.51158, "lon": 40.582827, "address": "улица Дежнёвцев, 34", "city": "Архангельск",
            "generic": False, "site_key": "door", "yandex_contractor_rows": "[]"}
    objs = ob([{"object_id": "a:0", "name": "KDV", **base}, {"object_id": "t:0", "name": "Табачная лавка", **base}])
    rec = {**yandex.parse(CARD, "u").to_dict(), "note": "", "checked": "2026-10-29", "site_key": "door"}
    rec["search_url"] = yandex.own_query(objs.loc["a:0"])
    ev = yandex.run(objs, cache={rec["search_url"]: rec})
    assert list(ev["object_id"]) == ["a:0"]
    assert yandex.own_query(objs.loc["t:0"]) != rec["search_url"]
