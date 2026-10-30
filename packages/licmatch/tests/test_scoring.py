"""The matrix must compute exactly what the one-pair formula says it does.

`pair_score()` is the published definition; `score_matrix()` is an optimisation
of it.  An optimisation that quietly disagrees with its definition is the whole
class of defect this test exists to catch, so the two are compared on every pair
of a small fixture rather than on a hand-picked one.
"""
from __future__ import annotations

import unittest

import numpy as np

from licmatch import (DEFAULTS, NAME_ALIAS, NAME_FUZZY, NAME_GENERIC,
                      ROUTE_ADDR, ROUTE_GEO, ROUTE_NONE, MatchParams, accepted,
                      address_score, brand_key, fascia_name, holder_name,
                      home_city_re, pair_score, parse_address_fields,
                      parse_address_line, score_matrix, side)

CITY = home_city_re("Архангельск")

# Four objects and four register rows, written the way each source writes them.
OBJECTS = [
    ("Пятёрочка, супермаркет", "Архангельск, улица Тимме, 2 к5", 64.5490, 40.5310),
    ("Магнит", "Архангельск, набережная Северной Двины, 55", 64.5350, 40.5170),
    ("Бристоль", "Архангельск, Ленинградский проспект, 356 к4", 64.5100, 40.5600),
    ("Причал, магазин", "Лесная речка, Лахтинское шоссе, 17", 64.6200, 40.4400),
]
REGISTRY = [
    ('ООО "Агроторг"', "Тимме Я. Улица", "2", "", "5", 64.5491, 40.5311),
    ('АО "Тандер"', "Северной Двины Набережная", "55", "", "", 64.5351, 40.5169),
    ('ООО "Альбион-2002"', "Ленинградский пр-кт", "356", "", "4", np.nan, np.nan),
    ('ООО "ЛОЙ"', "Лахтинское ш", "17", "Лесная речка", "", np.nan, np.nan),
]


def build_sides(params: MatchParams = DEFAULTS):
    a = side([parse_address_line(v[1], CITY) for v in OBJECTS],
             [fascia_name(v[0]) for v in OBJECTS],
             [v[2] for v in OBJECTS], [v[3] for v in OBJECTS])
    b = side([parse_address_fields(r[1], r[2], CITY, r[3], r[4]) for r in REGISTRY],
             [holder_name(r[0]) for r in REGISTRY],
             [r[5] for r in REGISTRY], [r[6] for r in REGISTRY])
    return a, b


class TestMatrixMatchesFormula(unittest.TestCase):
    def setUp(self):
        self.a, self.b = build_sides()
        self.top_a = [parse_address_line(v[1], CITY) for v in OBJECTS]
        self.top_b = [parse_address_fields(r[1], r[2], CITY, r[3], r[4])
                      for r in REGISTRY]
        self.s = score_matrix(self.a, self.b, DEFAULTS)

    def test_every_admitted_pair_agrees_with_pair_score(self):
        for i in range(len(OBJECTS)):
            for j in range(len(REGISTRY)):
                if self.s.method[i, j] == ROUTE_NONE:
                    self.assertTrue(np.isnan(self.s.score[i, j]))
                    continue
                d = (None if self.s.method[i, j] == ROUTE_ADDR
                     else float(self.s.distance[i, j]))
                expected = pair_score(self.top_a[i], self.top_b[j],
                                      self.a["name_key"][i], self.b["name_key"][j],
                                      d, DEFAULTS)
                with self.subTest(i=i, j=j):
                    self.assertAlmostEqual(float(self.s.score[i, j]), expected, places=5)

    def test_address_term_agrees_with_address_score(self):
        from licmatch import address_score
        for i in range(len(OBJECTS)):
            for j in range(len(REGISTRY)):
                with self.subTest(i=i, j=j):
                    self.assertAlmostEqual(
                        float(self.s.addr[i, j]),
                        address_score(self.top_a[i], self.top_b[j]), places=5)


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.a, self.b = build_sides()
        self.s = score_matrix(self.a, self.b, DEFAULTS)

    def test_true_pairs_are_on_the_diagonal(self):
        ok = accepted(self.s, DEFAULTS)
        for i in range(4):
            self.assertTrue(ok[i, i], f"pair {i} refused")

    def test_coordinate_rows_take_the_geo_route(self):
        self.assertEqual(self.s.method[0, 0], ROUTE_GEO)
        self.assertEqual(self.s.method[1, 1], ROUTE_GEO)

    def test_coordinateless_rows_take_the_address_route(self):
        self.assertEqual(self.s.method[2, 2], ROUTE_ADDR)
        self.assertEqual(self.s.method[3, 3], ROUTE_ADDR)
        self.assertTrue(np.isnan(self.s.distance[2, 2]))

    def test_distance_term_dropped_not_zeroed_on_the_address_route(self):
        # Scored as a zero, a perfect address+name pair would reach only 0.70
        # and the 0.75 "strong" tier would be unreachable without coordinates.
        self.assertGreater(float(self.s.score[3, 3]), 0.75)

    def test_far_away_pair_is_refused(self):
        far = np.isnan(self.s.score)
        self.assertTrue(far[0, 1] or not accepted(self.s, DEFAULTS)[0, 1])


class TestFloors(unittest.TestCase):
    def test_address_floor_refuses_the_neighbouring_building(self):
        # Same street, 8 m away, a different house number: the combined score
        # clears `accept`, and only the address floor stops it.
        a = side([parse_address_line("Архангельск, улица Тимме, 2", CITY)],
                 ["пятерочка"], [64.5490], [40.5310])
        b = side([parse_address_fields("Тимме Я. Улица", "4", CITY)],
                 ["пятерочка"], [64.5491], [40.5310])
        s = score_matrix(a, b, DEFAULTS)
        self.assertGreaterEqual(float(s.score[0, 0]), DEFAULTS.accept)
        self.assertFalse(bool(accepted(s, DEFAULTS)[0, 0]))
        unguarded = MatchParams(addr_min=0.0)
        self.assertTrue(bool(accepted(s, unguarded)[0, 0]))

    def test_params_reject_impossible_settings(self):
        with self.assertRaises(ValueError):
            MatchParams(w_addr=-1)
        with self.assertRaises(ValueError):
            MatchParams(max_m=0)
        with self.assertRaises(ValueError):
            MatchParams(w_addr=0, w_name=0)


class TestNameTerm(unittest.TestCase):
    def test_alias_bridges_holder_and_fascia(self):
        self.assertEqual(holder_name('АО "Тандер"'), "магнит")
        self.assertEqual(fascia_name("Магнит, супермаркет"), "магнит")

    def test_legal_boilerplate_dropped(self):
        self.assertEqual(
            holder_name('Общество с ограниченной ответственностью «ЛОЙ» '
                        'Сокращенно: ООО "ЛОЙ"'), "лой")

    def test_unknown_name_scores_but_does_not_decide(self):
        # An object whose name says nothing still matches on address alone:
        # the name weight is 0.20, the address floor does the work.
        a = side([parse_address_line("Архангельск, улица Тимме, 2 к5", CITY)],
                 [""], [64.5490], [40.5310])
        b = side([parse_address_fields("Тимме Я. Улица", "2", CITY, "", "5")],
                 ["агроторг"], [64.5491], [40.5311])
        s = score_matrix(a, b, DEFAULTS)
        self.assertEqual(float(s.name[0, 0]), 0.0)
        self.assertTrue(bool(accepted(s, DEFAULTS)[0, 0]))


# --------------------------------------------------------------------------
# generic fascias and known (fascia, holder) pairs
# --------------------------------------------------------------------------
# The same four doors, now under names that exercise the name term's two new
# cases: a generic fascia on each route, and a fascia whose holder is known.
OBJECTS_G = [
    ("Продукты", "Архангельск, улица Тимме, 2 к5", 64.5490, 40.5310),          # generic, geo
    ("Кафе", "Архангельск, Ленинградский проспект, 356 к4", 64.5100, 40.5600),  # reduces to "", address
    ("Союз", "Архангельск, набережная Северной Двины, 55", 64.5350, 40.5170),  # known holder
    ("Продукты", "Лесная речка, Лахтинское шоссе, 17", 64.6200, 40.4400),      # generic, address
]
# holder, street, house, settlement, block, lat, lon, tax number (made up)
REGISTRY_G = [
    ('ООО "Агроторг"', "Тимме Я. Улица", "2", "", "5", 64.5491, 40.5311, "2900000001"),
    ('ООО "Альбион-2002"', "Ленинградский пр-кт", "356", "", "4", np.nan, np.nan, "2900000002"),
    ('ООО "Северторг"', "Северной Двины Набережная", "55", "", "", 64.5351, 40.5169, "2900000003"),
    ('ООО "ЛОЙ"', "Лахтинское ш", "17", "Лесная речка", "", np.nan, np.nan, "2900000004"),
]
ALIASES = {("союз", "2900000003")}


def build_generic_sides():
    top_a = [parse_address_line(v[1], CITY) for v in OBJECTS_G]
    top_b = [parse_address_fields(r[1], r[2], CITY, r[3], r[4]) for r in REGISTRY_G]
    a = side(top_a, [fascia_name(v[0]) for v in OBJECTS_G],
             [v[2] for v in OBJECTS_G], [v[3] for v in OBJECTS_G])
    b = side(top_b, [holder_name(r[0]) for r in REGISTRY_G],
             [r[5] for r in REGISTRY_G], [r[6] for r in REGISTRY_G],
             alias_keys=[r[7] for r in REGISTRY_G])
    return a, b, top_a, top_b


def plain(adr, nam, d, p=DEFAULTS):
    """The v1.0 formula, written out with no special cases."""
    if d is None:
        return (p.w_addr * adr + p.w_name * nam) / (p.w_addr + p.w_name)
    return p.w_addr * adr + p.w_name * nam + p.w_dist * (1 - d / p.max_m)


class TestMatrixMatchesFormulaGenericAndAlias(unittest.TestCase):
    """The agreement test, repeated on the fixture that reaches the new branches."""

    def check(self, params, aliases):
        a, b, top_a, top_b = build_generic_sides()
        s = score_matrix(a, b, params, aliases=aliases)
        routes = set()
        for i in range(len(OBJECTS_G)):
            for j in range(len(REGISTRY_G)):
                if s.method[i, j] == ROUTE_NONE:
                    self.assertTrue(np.isnan(s.score[i, j]))
                    continue
                routes.add(int(s.method[i, j]))
                d = None if s.method[i, j] == ROUTE_ADDR else float(s.distance[i, j])
                alias = (brand_key(a["alias_key"][i]), b["alias_key"][j]) in set(aliases)
                expected = pair_score(top_a[i], top_b[j], a["name_key"][i],
                                      b["name_key"][j], d, params, alias=alias)
                with self.subTest(i=i, j=j):
                    self.assertAlmostEqual(float(s.score[i, j]), expected, places=5)
        self.assertEqual(routes, {ROUTE_GEO, ROUTE_ADDR}, "fixture must reach both routes")

    def test_agree_with_neutral_generic(self):
        self.check(DEFAULTS, ALIASES)

    def test_agree_without_neutral_generic(self):
        self.check(MatchParams(neutral_generic=False), ALIASES)

    def test_agree_when_an_alias_names_a_generic_fascia(self):
        self.check(DEFAULTS, ALIASES | {("продукты", "2900000001"),
                                        ("продукты", "2900000004")})


class TestGenericFascia(unittest.TestCase):
    def setUp(self):
        self.a, self.b, _, _ = build_generic_sides()
        self.s = score_matrix(self.a, self.b, DEFAULTS, aliases=ALIASES)

    def test_geo_route_hands_the_name_weight_to_the_others(self):
        s, p = self.s, DEFAULTS
        self.assertEqual(s.method[0, 0], ROUTE_GEO)
        dsc = 1 - float(s.distance[0, 0]) / p.max_m
        expected = ((p.w_addr * float(s.addr[0, 0]) + p.w_dist * dsc)
                    * (p.w_addr + p.w_name + p.w_dist) / (p.w_addr + p.w_dist))
        self.assertAlmostEqual(float(s.score[0, 0]), expected, places=5)
        self.assertEqual(s.name_kind[0, 0], NAME_GENERIC)

    def test_address_route_is_the_address_term_alone(self):
        for i in (1, 3):
            with self.subTest(i=i):
                self.assertEqual(self.s.method[i, i], ROUTE_ADDR)
                self.assertAlmostEqual(float(self.s.score[i, i]),
                                       float(self.s.addr[i, i]), places=6)
                self.assertEqual(self.s.name_kind[i, i], NAME_GENERIC)

    def test_generic_true_pairs_are_admitted(self):
        ok = accepted(self.s, DEFAULTS)
        for i in (0, 1, 3):
            self.assertTrue(ok[i, i], f"pair {i} refused")

    def test_switch_off_restores_the_plain_formula(self):
        p = MatchParams(neutral_generic=False)
        s = score_matrix(self.a, self.b, p)
        self.assertTrue((s.name_kind == NAME_FUZZY).all())
        self.assertAlmostEqual(
            float(s.score[0, 0]),
            plain(float(s.addr[0, 0]), float(s.name[0, 0]), float(s.distance[0, 0]), p),
            places=5)
        # "Кафе" reduced to nothing: scored as a zero name, not dropped.
        self.assertEqual(float(s.name[1, 1]), 0.0)
        self.assertAlmostEqual(float(s.score[1, 1]),
                               plain(float(s.addr[1, 1]), 0.0, None, p), places=5)
        self.assertLess(float(s.score[1, 1]), float(self.s.score[1, 1]))


class TestNonGenericUnchanged(unittest.TestCase):
    """v1.1 must leave every pair without a generic fascia where v1.0 put it."""

    def test_default_score_is_the_plain_formula(self):
        a, b = build_sides()
        s = score_matrix(a, b, DEFAULTS)
        self.assertTrue((s.name_kind == NAME_FUZZY).all())
        for i in range(len(OBJECTS)):
            for j in range(len(REGISTRY)):
                if s.method[i, j] == ROUTE_NONE:
                    continue
                d = None if s.method[i, j] == ROUTE_ADDR else float(s.distance[i, j])
                with self.subTest(i=i, j=j):
                    self.assertAlmostEqual(
                        float(s.score[i, j]),
                        plain(float(s.addr[i, j]), float(s.name[i, j]), d), places=5)

    def test_switch_changes_nothing_for_named_shops(self):
        a, b = build_sides()
        on = score_matrix(a, b, DEFAULTS)
        off = score_matrix(a, b, MatchParams(neutral_generic=False))
        np.testing.assert_array_equal(on.score, off.score)

    def test_pair_score_matches_the_plain_formula(self):
        top_a = parse_address_line(OBJECTS[0][1], CITY)
        top_b = parse_address_fields("Тимме Я. Улица", "2", CITY, "", "5")
        from rapidfuzz import fuzz
        nam = fuzz.WRatio("ромашка", "агроторг") / 100.0
        adr = address_score(top_a, top_b)
        self.assertAlmostEqual(pair_score(top_a, top_b, "ромашка", "агроторг", 12.0),
                               plain(adr, nam, 12.0), places=9)
        self.assertAlmostEqual(pair_score(top_a, top_b, "ромашка", "агроторг", None),
                               plain(adr, nam, None), places=9)


class TestAlias(unittest.TestCase):
    def test_known_pair_scores_name_one(self):
        a, b, _, _ = build_generic_sides()
        s = score_matrix(a, b, DEFAULTS, aliases=ALIASES)
        self.assertEqual(float(s.name[2, 2]), 1.0)
        self.assertEqual(s.name_kind[2, 2], NAME_ALIAS)
        without = score_matrix(a, b, DEFAULTS)
        self.assertLess(float(without.name[2, 2]), 1.0)
        self.assertGreater(float(s.score[2, 2]), float(without.score[2, 2]))

    def test_lookup_is_by_alias_key_not_by_name(self):
        # Two companies of one name at one address, told apart by tax number:
        # only the one the alias names gets the name term.
        a = side([parse_address_line("Архангельск, набережная Северной Двины, 55", CITY)],
                 [fascia_name("Союз")], [64.5350], [40.5170])
        b = side([parse_address_fields("Северной Двины Набережная", "55", CITY)] * 2,
                 [holder_name('ООО "Северторг"')] * 2,
                 [64.5351] * 2, [40.5169] * 2,
                 alias_keys=["2900000003", "2900000099"])
        s = score_matrix(a, b, DEFAULTS, aliases=ALIASES)
        self.assertEqual(list(s.name_kind[0]), [NAME_ALIAS, NAME_FUZZY])
        self.assertEqual(float(s.name[0, 0]), 1.0)
        self.assertLess(float(s.name[0, 1]), 1.0)
        # And the holder's *name* on its own is not a key at all.
        s2 = score_matrix(a, b, DEFAULTS, aliases={("союз", "северторг")})
        self.assertTrue((s2.name_kind == NAME_FUZZY).all())

    def test_alias_key_defaults_to_name_key(self):
        a = side([parse_address_line("Архангельск, набережная Северной Двины, 55", CITY)],
                 [fascia_name("Союз")], [64.5350], [40.5170])
        b = side([parse_address_fields("Северной Двины Набережная", "55", CITY)],
                 [holder_name('ООО "Северторг"')], [64.5351], [40.5169])
        self.assertEqual(list(b["alias_key"]), ["северторг"])
        s = score_matrix(a, b, DEFAULTS, aliases={("союз", "северторг")})
        self.assertEqual(s.name_kind[0, 0], NAME_ALIAS)

    def test_learned_ampersand_key_reaches_the_side(self):
        # learn_brands() counts fascias by brand_key, so it hands back
        # "красное белое"; the side carries name_key "красное&белое".
        a = side([parse_address_line("Архангельск, улица Тимме, 2 к5", CITY)],
                 [fascia_name("Красное&Белое")], [64.5490], [40.5310])
        b = side([parse_address_fields("Тимме Я. Улица", "2", CITY, "", "5")],
                 [holder_name('ООО "Лабиринт-М"')], [64.5491], [40.5311],
                 alias_keys=["2900000005"])
        s = score_matrix(a, b, DEFAULTS, aliases={("красное белое", "2900000005")})
        self.assertEqual(s.name_kind[0, 0], NAME_ALIAS)

    def test_alias_outranks_generic(self):
        a, b, _, _ = build_generic_sides()
        s = score_matrix(a, b, DEFAULTS, aliases={("продукты", "2900000001")})
        self.assertEqual(s.name_kind[0, 0], NAME_ALIAS)
        self.assertEqual(s.name_kind[3, 3], NAME_GENERIC)
        self.assertAlmostEqual(
            float(s.score[0, 0]),
            plain(float(s.addr[0, 0]), 1.0, float(s.distance[0, 0])), places=5)

    def test_pair_score_alias_on_the_address_route(self):
        top = parse_address_line("Архангельск, Ленинградский проспект, 356 к4", CITY)
        reg = parse_address_fields("Ленинградский пр-кт", "356", CITY, "", "4")
        adr = address_score(top, reg)
        self.assertAlmostEqual(pair_score(top, reg, "союз", "северторг", None, alias=True),
                               plain(adr, 1.0, None), places=9)


if __name__ == "__main__":
    unittest.main()
