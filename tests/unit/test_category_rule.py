"""The generic category rule (`categories.base`) on hand-made Evidence.

Uses the real catering block of config.yaml, so these also guard the config's
shape; the parity test covers the numbers.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from infracheck.categories import base, catering
from infracheck.core import evidence

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def spec() -> base.Spec:
    return catering.spec(yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8")))


def ev(*rows) -> pd.DataFrame:
    return evidence.make([{"identity": "strong", "record_state": "current"} | r for r in rows])


IDX = pd.Index(["a", "b"], name="object_id")


def test_one_count_per_group_and_export_never_counts(spec):
    e = ev({"object_id": "a", "check": "yandex", "source": "yandex", "label": "Яндекс", "text": "own"},
           {"object_id": "a", "check": "yandex_contractor", "source": "yandex", "label": "Яндекс", "text": "con"},
           {"object_id": "a", "check": "export_name", "source": "export", "kind_text": "кафе",
            "kind_scheme": "ru_text", "label": "название"})
    out = base.existence(e, IDX, spec)
    assert out.loc["a", "n_sources"] == 1 and out.loc["a", "sources"] == "Яндекс"
    assert out.loc["a", "confirmed"] == 0 and out.loc["b", "n_sources"] == 0


def test_reasons_order_and_con_clauses(spec):
    e = ev({"object_id": "a", "check": "yandex_contractor", "source": "yandex", "label": "Яндекс", "text": "con"},
           {"object_id": "a", "check": "providers", "source": "osm", "label": "OSM", "text": "запись"},
           {"object_id": "a", "check": "yandex", "source": "yandex", "label": "Яндекс", "text": "own"},
           {"object_id": "a", "check": "foursquare", "source": "fsq", "record_state": "stale",
            "label": "Foursquare", "text": "old"})
    r = base.reasons(e, IDX, spec)
    assert r["a"] == "OSM: запись | Яндекс: own; con || против/не найдено: Foursquare: old"
    assert r["b"] == "источников нет"


def test_foursquare_only_overture_is_silent(spec):
    e = ev({"object_id": "a", "check": "overture", "source": "overture", "origin": "Foursquare|Overture",
            "label": "Overture", "text": "x"})
    assert base.existence(e, IDX, spec).loc["a", "n_sources"] == 0
    assert base.reasons(e, IDX, spec)["a"] == "источников нет"
    # Any other upstream dataset makes it a source of its own.
    for origin in ("meta|Overture", "AllThePlaces|Overture", "Foursquare|meta|Overture"):
        e.loc[0, "origin"] = origin
        assert base.existence(e, IDX, spec).loc["a", "n_sources"] == 1, origin


def test_weak_foursquare_only_overture_still_speaks(spec):
    """Exclusion drops only rows that would count; a lost record stays after « || »."""
    e = ev({"object_id": "a", "check": "overture", "source": "overture", "origin": "Foursquare|Overture",
            "identity": "weak", "label": "Overture", "text": "lost"})
    assert base.reasons(e, IDX, spec)["a"] == "источников нет || против/не найдено: Overture: lost"


@pytest.mark.parametrize("text,role", [
    ("Кофейный автомат", "vending"), ("кафедра истории", "no"), ("магазин-кафе", "yes"),
    ("универсальный магазин", "no"), ("служба доставки", ""),
])
def test_ru_ladder_order_is_the_rule(spec, text, role):
    assert base.classify(text, spec.kind.schemes["ru_text"]) == role


@pytest.mark.parametrize("roles,verdict", [
    ([], "none"), (["", ""], "none"), (["yes"], "yes"), (["yes", "no"], "conflict"),
    (["vending", "no"], "no"), (["vending"], "vending"), (["vending", "yes", "no"], "conflict"),
])
def test_fold_votes(roles, verdict):
    assert base.fold_votes(roles) == verdict


def test_kind_reasons_text(spec):
    e = ev({"object_id": "a", "check": "export_name", "source": "export", "kind_text": "кофейня",
            "kind_scheme": "ru_text", "label": "2GIS, описание в названии"},
           {"object_id": "a", "check": "foursquare", "source": "fsq", "kind_text": "Retail > Shop",
            "kind_scheme": "fsq_category", "label": "Foursquare"})
    k = base.kind(e, IDX, spec)
    assert k.loc["a", "food"] == "противоречие"
    assert k.loc["a", "food_reasons"] == ("2GIS, описание в названии: «кофейня» → да | "
                                          "Foursquare: «Retail > Shop» → нет")
    assert k.loc["b", "food"] == "нет данных"


def test_weak_record_casts_no_kind_vote(spec):
    """Since 2026-10-30: a record another object won (weak) says nothing about this one's kind."""
    assert spec.kind.vote_identity == ("strong",)
    e = ev({"object_id": "a", "check": "export_name", "source": "export", "kind_text": "кофейня",
            "kind_scheme": "ru_text", "label": "2GIS, описание в названии"},
           {"object_id": "a", "check": "foursquare", "source": "fsq", "identity": "weak", "kind_text": "Retail > Shop",
            "kind_scheme": "fsq_category", "label": "Foursquare"})
    k = base.kind(e, IDX, spec)
    assert k.loc["a", "food"] == "да"
    assert k.loc["a", "food_reasons"] == "2GIS, описание в названии: «кофейня» → да"


def test_needs_query_skips_contractor_hit(spec):
    e = ev({"object_id": "a", "check": "yandex_contractor", "source": "yandex", "label": "Яндекс", "text": "c"},
           {"object_id": "b", "check": "providers", "source": "osm", "label": "OSM", "text": "x"})
    q = base.needs_query(e, IDX, spec)
    assert not q["a"] and q["b"]


def test_merged_components_and_summary(spec):
    """Relink twins are one object: chains merge, the lowest entity names it, counts are by merged object."""
    idx = pd.Index(["a", "b", "c", "d"], name="object_id")
    objects = pd.DataFrame({"entity": [3, 1, 2, 0]}, index=idx)
    e = ev({"object_id": "a", "check": "relink", "source": "osm", "relink_object": "b"},
           {"object_id": "c", "check": "relink", "source": "2gis", "relink_object": "b"},
           {"object_id": "b", "check": "relink", "source": "2gis", "relink_object": "a"})
    m = base.merged(e, objects, spec)
    assert m.to_dict() == {"a": 1, "b": 1, "c": 1, "d": 0}
    table = pd.DataFrame({"merged": m, "confirmed": [1, 1, 1, 0], "sources": ["OSM", "OSM", "OSM", ""]}
                         | {g.column: False for g in spec.groups}, index=idx)
    table["src_osm"] = [True, True, True, False]
    assert base.disagreeing(table, spec) == 0
    assert base._value({"value": "objects"}, table, table, spec) == "2 (записей 4)"
    assert base._value({"value": "confirmed_objects"}, table, table, spec) == "1 из 2 (50.0%); записей 3 из 4"
    assert base._value({"count": "src_osm"}, table, table, spec) == "1 (записей 3)"
    assert base.combinations(table, spec).to_dict() == {"OSM": 1, "—": 1}
    table.loc["c", "confirmed"] = 0
    assert base.disagreeing(table, spec) == 1
    assert base._value({"value": "confirmed_objects"}, table, table, spec).endswith(
        "; у 1 склеенных объектов записи расходятся")
