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
    assert rl.iloc[0]["relink_object"] == "b"  # the twin: merged with "a" when objects are counted
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


def test_contains_words_medicine_full_names():
    from infracheck.core.names import contains_words
    assert contains_words("Окродент", "Стоматология Окродент")
    assert contains_words("Центр ядерной медицины", "Смкц имени Н. А. Семашко, центр ядерной медицины")
    assert contains_words("Городская больница No 4", "Архангельская городская клиническая больница № 4")
    assert not contains_words("Взрослая поликлиника", "Первая городская больница имени Волосевич Е. Е., поликлиника")
    assert not contains_words("Продукты", "Продукты у дома")  # trade-only fascia never qualifies


def test_judge_contain_rule_is_opt_in_and_short_range():
    page = """<div data-object="search-list-item" data-id="7" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Стоматология Окродент</div>
<div class="search-business-snippet-view__address">пр. Сибиряковцев, 1</div></div>"""
    a = yandex.parse(page)
    assert yandex.judge(a, "Окродент", 64.5, 40.5)["status"] == yandex.STATUS_ABSENT
    on = yandex.Policy(contain_m=60.0)
    assert yandex.judge(a, "Окродент", 64.5, 40.5, policy=on)["status"] == yandex.STATUS_PRESENT
    assert yandex.judge(a, "Окродент", 64.5012, 40.5, policy=on)["status"] == yandex.STATUS_ABSENT  # ~130 m
    rows = json.dumps([["Стоматология Окродент", "пр. Сибиряковцев, 1", "http://y/7"]])
    assert not yandex.rejudge_contractor(rows, "Окродент")["ok"]
    assert yandex.rejudge_contractor(rows, "Окродент", policy=on)["ok"]


def test_number_agreement():
    from infracheck.core.names import number_agreement
    assert number_agreement("Детский сад No101 комбинированного вида", "Детский сад № 101") is True
    assert number_agreement("Средняя школа No 34", "Средняя школа № 43") is False
    assert number_agreement("Школа No 59", "Средняя школа № 59") is True
    assert number_agreement("Магнит", "Магнит у дома") is None
    assert number_agreement("Поликлиника No 1", "Городская больница № 1") is None  # different kinds
    # review 2026-11-24: the number belongs to the kind the name opens with
    assert number_agreement("Средняя школа No60", "Детский сад при школе № 60") is None
    assert number_agreement("Детская школа искусств № 2", "Средняя школа № 2") is None
    assert number_agreement("Дом детского творчества № 1", "Детский сад № 1") is None
    assert number_agreement("Школа № 5, корпус № 2", "Средняя школа № 2") is False
    assert number_agreement("Городская больница No 4", "Архангельская ГКБ № 4, поликлиника № 4") is True
    assert number_agreement("Аптека № 05", "Аптека № 5") is True
    assert number_agreement("Аптека № 2", "Аптека 36,6") is None
    assert number_agreement("Techno 3", "Techno 4") is None


def test_judge_numbers_policy():
    page = """<div data-object="search-list-item" data-id="9" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Средняя школа № 43</div>
<div class="search-business-snippet-view__address">ул. А, 1</div></div>
<div data-object="search-list-item" data-id="8" data-coordinates="40.5,64.5004">
<div class="search-business-snippet-view__title">Детский сад № 101</div>
<div class="search-business-snippet-view__address">ул. Б, 2</div></div>"""
    a = yandex.parse(page)
    off, on = yandex.Policy(), yandex.Policy(numbers=True)
    assert yandex.judge(a, "Средняя школа No 34", 64.5, 40.5, policy=off)["status"] == yandex.STATUS_PRESENT
    assert yandex.judge(a, "Средняя школа No 34", 64.5, 40.5, policy=on)["status"] == yandex.STATUS_ABSENT
    assert yandex.judge(a, "Детский сад No101 комбинированного вида", 64.5, 40.5, policy=off)["status"] \
        == yandex.STATUS_ABSENT
    assert yandex.judge(a, "Детский сад No101 комбинированного вида", 64.5, 40.5, policy=on)["status"] \
        == yandex.STATUS_PRESENT


def test_paused_policy():
    page = """<div data-object="search-list-item" data-id="3" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Штаб</div>
<div class="search-business-snippet-view__address">ул. Ленина, 1</div> Временно не работает</div>"""
    a = yandex.parse(page)
    assert a.candidates[0].paused and not a.candidates[0].closed
    assert yandex.judge(a, "Штаб", 64.5, 40.5)["status"] == yandex.STATUS_CLOSED
    v = yandex.judge(a, "Штаб", 64.5, 40.5, policy=yandex.Policy(paused_is_closed=False))
    assert v["status"] == yandex.STATUS_PRESENT and v["yandex_paused"]


def test_fallback_query_for_vague_address():
    r = pd.Series({"name": "Магнит", "address": "Остановка", "lat": 64.5, "lon": 40.5, "city": "Архангельск",
                   "generic": False, "site_key": "k"})
    hit = """<div data-object="search-list-item" data-id="5" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Магнит</div>
<div class="search-business-snippet-view__address">ул. В, 3</div></div>"""
    miss = """<div data-object="search-list-item" data-id="6" data-coordinates="40.5,64.5">
<div class="search-business-snippet-view__title">Остановка Площадь</div>
<div class="search-business-snippet-view__address">ул. В</div></div>"""
    cache = {yandex.own_query(r): {**yandex.parse(miss, yandex.own_query(r)).to_dict(), "note": "", "checked": "d1"},
             yandex.fallback_query(r): {**yandex.parse(hit, yandex.fallback_query(r)).to_dict(), "note": "",
                                        "checked": "d2"}}
    assert yandex.vague_address("Остановка") and not yandex.vague_address("Ленина, 5") and not yandex.vague_address("")
    assert yandex.vague_address("Посёлок 26-го Лесозавода") and yandex.vague_address("3-й Пятилетки")
    assert not yandex.vague_address("nan") and not yandex.vague_address(float("nan"))
    rec, v = yandex._answer(r, cache, yandex.DEFAULT)
    assert v["status"] == yandex.STATUS_ABSENT  # off by default
    rec, v = yandex._answer(r, cache, yandex.Policy(fallback_vague_address=True))
    assert v["status"] == yandex.STATUS_PRESENT and rec["checked"] == "d2"
    # a hit of the first query is never replaced
    cache[yandex.own_query(r)] = cache[yandex.fallback_query(r)] | {"checked": "d1"}
    rec, v = yandex._answer(r, cache, yandex.Policy(fallback_vague_address=True))
    assert rec["checked"] == "d1"


def _list_page(*items):
    """A Yandex result list: (oid, name, lon, lat, closed)."""
    out = []
    for oid, name, lon, lat, closed in items:
        out.append(f'''<div data-object="search-list-item" data-id="{oid}" data-coordinates="{lon},{lat}">
<div class="search-business-snippet-view__title">{name}</div>
<div class="search-business-snippet-view__address">ул. А, 1</div>{" Больше не работает" if closed else ""}</div>''')
    return "\n".join(out)


def test_lifted_match_never_closes():
    a = yandex.parse(_list_page(("1", "Стоматология Окродент", 40.5, 64.5, True)))
    v = yandex.judge(a, "Окродент", 64.5, 40.5, policy=yandex.Policy(contain_m=60.0))
    assert v["status"] == yandex.STATUS_ABSENT  # a containment match may confirm, never close
    a = yandex.parse(_list_page(("1", "Городская поликлиника № 2", 40.5, 64.5, False)))
    v = yandex.judge(a, "Городская поликлиника № 1", 64.5, 40.5, policy=yandex.Policy(contain_m=60.0, numbers=True))
    assert v["status"] == yandex.STATUS_ABSENT  # containment respects the number veto


def test_prefer_open_within_limit():
    page = _list_page(("1", "Апрель", 40.5, 64.50005, True), ("2", "Апрель", 40.5, 64.5004, False))  # ~6 m, ~45 m
    a = yandex.parse(page)
    assert yandex.judge(a, "Апрель", 64.5, 40.5)["status"] == yandex.STATUS_CLOSED
    assert yandex.judge(a, "Апрель", 64.5, 40.5, policy=yandex.Policy(prefer_open_m=60.0))["status"] \
        == yandex.STATUS_PRESENT
    far = yandex.parse(_list_page(("1", "Апрель", 40.5, 64.50005, True), ("2", "Апрель", 40.5, 64.5008, False)))
    assert yandex.judge(far, "Апрель", 64.5, 40.5, policy=yandex.Policy(prefer_open_m=60.0))["status"] \
        == yandex.STATUS_CLOSED  # the open one ~90 m away is another branch


def _objs(*rows):
    base = {"city": "Архангельск", "generic": False, "yandex_contractor_rows": "[]"}
    return ob([{**base, **r} for r in rows])


def test_one_card_one_site():
    page = _list_page(("105", "Бристоль", 40.5, 64.5, False))
    objs = _objs({"object_id": "d:0", "name": "Бристоль", "lat": 64.50005, "lon": 40.5, "address": "А, 107",
                  "site_key": "k0"},
                 {"object_id": "d:1", "name": "Бристоль", "lat": 64.5006, "lon": 40.5, "address": "А, 118",
                  "site_key": "k1"})
    cache = {yandex.own_query(r): {**yandex.parse(page, yandex.own_query(r)).to_dict(), "note": "", "checked": "d"}
             for _, r in objs.iterrows()}
    both = yandex.run(objs, cache=cache)
    assert (both.loc[both["check"].eq("yandex"), "identity"] == "strong").all()
    ev = yandex.run(objs, yandex.Policy(exclusive_m=50.0), cache=cache)
    own = ev[ev["check"].eq("yandex")].set_index("object_id")
    assert own.at["d:0", "identity"] == "strong" and own.at["d:1", "identity"] == "none"
    assert own.at["d:1", "yandex_status"] == yandex.STATUS_ABSENT and own.at["d:1", "ref"] == ""


def test_closure_withdraws_contractor_hit_on_same_card():
    url = "https://yandex.ru/maps/org/agat/1761946217/"
    page = _list_page(("1761946217", "Агат", 40.5, 64.5, True))
    objs = _objs({"object_id": "d:0", "name": "Агат", "lat": 64.5, "lon": 40.5, "address": "Валявкина, 13",
                  "site_key": "k0", "yandex_contractor_rows": json.dumps([["Агат", "Валявкина, 13", url]])})
    cache = {yandex.own_query(objs.iloc[0]): {**yandex.parse(page, "u").to_dict(), "note": "", "checked": "d"}}
    ev = yandex.run(objs, cache=cache).set_index("check")
    assert ev.at["yandex_contractor", "identity"] == "strong"  # off by default
    ev = yandex.run(objs, yandex.Policy(closed_overrides_contractor=True), cache=cache).set_index("check")
    assert ev.at["yandex_contractor", "identity"] == "weak" and ev.at["yandex", "yandex_status"] == "closed"
    assert yandex.org_id(url) == "1761946217" and yandex.org_id("https://yandex.ru/maps/org/42/") == "42"


def test_address_kind_rule():
    from infracheck.core.address import address_key, same_address
    assert same_address("Красина, 3к1", "Архангельск, Цигломенский округ, улица Красина, 3, корп. 1")
    assert not same_address("Капитана Хромцова, 5к1", "улица Капитана Хромцова, 5")
    assert address_key("Микрорайон Соломбала") is None
    page = """<div data-object="search-list-item" data-id="7" data-coordinates="40.5,64.5001">
<div class="search-business-snippet-view__title">Первая городская больница имени Волосевич Е. Е., поликлиника</div>
<div class="search-business-snippet-view__address">ул. Гайдара, 3</div></div>"""
    a = yandex.parse(page)
    on = yandex.Policy(address_kind=True)
    assert yandex.judge(a, "Взрослая поликлиника", 64.5, 40.5, policy=on, address="Гайдара, 5")["status"] \
        == yandex.STATUS_ABSENT  # another house
    assert yandex.judge(a, "Взрослая поликлиника", 64.5, 40.5, address="Гайдара, 3")["status"] \
        == yandex.STATUS_ABSENT  # off by default
    assert yandex.judge(a, "Взрослая поликлиника", 64.5, 40.5, policy=on, address="Гайдара, 3")["status"] \
        == yandex.STATUS_PRESENT
    assert yandex.judge(a, "Фармомед", 64.5, 40.5, policy=on, address="Гайдара, 3")["status"] \
        == yandex.STATUS_ABSENT  # a brand is never matched by address


def test_address_kind_loose_and_public():
    from infracheck.core.address import same_building
    from infracheck.core.names import trade_kind
    assert same_building("Красина, 3к1", "Архангельск, улица Красина, 3, 163044")
    assert same_building("Красина, 3к1", "улица Красина, 3, корп. 1")
    assert not same_building("Красина, 3к1", "улица Красина, 3, корп. 2")
    assert not same_building("Красина, 6", "улица Красина, 6а")
    assert not same_building("Красина, 3к1", "улица Гайдара, 3")
    assert trade_kind("Исакогорская подстанция скорой медицинской помощи") == ""
    assert trade_kind("Исакогорская подстанция скорой медицинской помощи", public_places=True) == "ambulance"
    assert trade_kind("Добрая аптека", public_places=True) == ""  # a chain, not a place
    assert trade_kind('Поликлиника "Здоровье"', public_places=True) == ""
    assert trade_kind("Поликлиника Медлайн", public_places=True) == ""
    assert trade_kind("Отделение врача общей практики") == "general_practice"
    assert trade_kind("Офис врача общей практики посёлка Экономия", public_places=True) == ""

    def page(name, address, lon="40.5"):
        return yandex.parse(f"""<div data-object="search-list-item" data-id="7" data-coordinates="{lon},64.5">
<div class="search-business-snippet-view__title">{name}</div>
<div class="search-business-snippet-view__address">{address}</div></div>""")

    strict = yandex.Policy(address_kind=True, numbers=True)
    loose = yandex.Policy(address_kind=True, numbers=True, address_loose_m=30.0)
    a = page("ГКБ имени Е. Е. Волосевич, поликлиника", "Архангельск, улица Красина, 3, 163044")
    args = ("Взрослая поликлиника", 64.5, 40.5)
    assert yandex.judge(a, *args, policy=strict, address="Красина, 3к1")["status"] == yandex.STATUS_ABSENT
    assert yandex.judge(a, *args, policy=loose, address="Красина, 3к1")["status"] == yandex.STATUS_PRESENT
    far = page("ГКБ имени Е. Е. Волосевич, поликлиника", "улица Красина, 3", lon="40.5008")  # ~38 m
    assert yandex.judge(far, *args, policy=loose, address="Красина, 3к1")["status"] == yandex.STATUS_ABSENT
    bare = page("Готовые очки", "")
    assert yandex.judge(bare, "Дисконт-магазин готовых очков", 64.5, 40.5, policy=loose,
                        address="Ленинградский проспект, 40к3")["status"] == yandex.STATUS_PRESENT
    assert yandex.judge(bare, "Дисконт-магазин готовых очков", 64.5, 40.5, policy=strict,
                        address="Ленинградский проспект, 40к3")["status"] == yandex.STATUS_ABSENT
    sub = page("ГБУЗ АО Архангельская областная клиническая станция скорой медицинской помощи",
               "Архангельск, Исакогорский округ, Бассейная улица, 8, 163035")
    assert yandex.judge(sub, "Исакогорская подстанция скорой медицинской помощи", 64.5, 40.5, policy=strict,
                        address="Бассейная, 8")["status"] == yandex.STATUS_PRESENT
    other = page("Аптека № 5", "улица Гайдара, 3")
    assert yandex.judge(other, "Аптека № 2", 64.5, 40.5, policy=loose, address="Гайдара, 3")["status"] \
        == yandex.STATUS_ABSENT  # the number veto stands against the address rule
