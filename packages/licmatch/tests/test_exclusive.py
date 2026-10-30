"""One outlet, one object: exclusivity, eligibility and ranked preferences.

The first class works on real addresses, the way the rest of the suite does.
The mechanics after it are easier to read on a hand-written score matrix --
every admitted pair has a perfect address, so only the numbers in the matrix
decide -- built by `scores()` below.
"""
from __future__ import annotations

import unittest

import numpy as np

from licmatch import (DEFAULTS, NAME_FUZZY, ROUTE_GEO, ROUTE_NONE, Scores,
                      fascia_name, greedy_assign, holder_name, home_city_re,
                      parse_address_fields, parse_address_line, score_matrix,
                      select, side)

CITY = home_city_re("Архангельск")
N = np.nan


def scores(matrix) -> Scores:
    """A Scores whose admitted pairs are the non-NaN cells of `matrix`."""
    m = np.asarray(matrix, dtype=np.float32)
    admitted = ~np.isnan(m)
    ones = np.where(admitted, 1.0, 0.0).astype(np.float32)
    return Scores(score=m, addr=ones, name=np.zeros_like(m), street=ones,
                  house=ones, distance=np.where(admitted, 0.0, np.nan).astype(np.float32),
                  method=np.where(admitted, ROUTE_GEO, ROUTE_NONE).astype(np.int8),
                  name_kind=np.full(m.shape, NAME_FUZZY, dtype=np.int8))


class TestOneOutletThreeObjects(unittest.TestCase):
    """Three shops in one building, one licence filed there."""

    ADDRESS = "Архангельск, улица Тимме, 2 к5"
    NAMES = ["Ромашка, магазин", "Пятёрочка, супермаркет", "Дымок, киоск"]
    WINNER = 1      # the one whose name the alias bridges to the holder

    def setUp(self):
        a = side([parse_address_line(self.ADDRESS, CITY)] * 3,
                 [fascia_name(n) for n in self.NAMES], [64.5490] * 3, [40.5310] * 3)
        b = side([parse_address_fields("Тимме Я. Улица", "2", CITY, "", "5")],
                 [holder_name('ООО "Агроторг"')], [64.5491], [40.5311])
        self.s = score_matrix(a, b, DEFAULTS)

    def test_all_three_are_admitted(self):
        sel = select(self.s, DEFAULTS)
        self.assertTrue(sel.accepted[:, 0].all())
        self.assertEqual(int(np.nanargmax(self.s.score[:, 0])), self.WINNER)

    def test_without_units_everyone_quotes_the_one_licence(self):
        sel = select(self.s, DEFAULTS)
        self.assertTrue(sel.hit.all())
        self.assertEqual(list(sel.pick), [0, 0, 0])
        self.assertFalse(sel.taken.any())

    def test_with_units_only_the_strongest_gets_it(self):
        sel = select(self.s, DEFAULTS, units=["L-1"])
        self.assertEqual(list(sel.hit), [False, True, False])
        self.assertEqual(list(sel.taken), [True, False, True])
        self.assertEqual(list(sel.rival), [self.WINNER, -1, self.WINNER])
        self.assertEqual(list(sel.name_kind), ["", "fuzzy", ""])
        self.assertTrue(np.isnan(sel.score[0]) and np.isnan(sel.score[2]))
        self.assertEqual(sel.method[0], "")

    def test_losers_still_count_the_licence(self):
        # Exclusivity is about what is quoted, not about what stands there.
        sel = select(self.s, DEFAULTS, units=["L-1"], keys=["L-1"],
                     groups={"matched": None})
        self.assertEqual(list(sel.counts["matched"]), [1, 1, 1])


class TestExclusivity(unittest.TestCase):
    def test_falls_back_to_the_next_free_unit(self):
        s = scores([[0.90, 0.80],
                    [0.95, N]])
        sel = select(s, DEFAULTS, units=["U1", "U2"])
        self.assertEqual(list(sel.pick), [1, 0])
        self.assertTrue(sel.hit.all())
        self.assertFalse(sel.taken.any())
        self.assertEqual(list(sel.rival), [-1, -1])

    def test_rows_sharing_a_unit_label_are_one_unit(self):
        # One licence filed twice (a status change) is still one outlet.
        s = scores([[0.90, 0.85],
                    [0.80, 0.88]])
        shared = select(s, DEFAULTS, units=["L-1", "L-1"])
        self.assertEqual(list(shared.hit), [True, False])
        self.assertEqual(list(shared.rival), [-1, 0])
        separate = select(s, DEFAULTS, units=["L-1", "L-2"])
        self.assertTrue(separate.hit.all())
        self.assertEqual(list(separate.pick), [0, 1])

    def test_partition_lets_two_layers_claim_one_unit(self):
        s = scores([[0.90],
                    [0.80]])
        one = select(s, DEFAULTS, units=["L-1"])
        self.assertEqual(list(one.hit), [True, False])
        two = select(s, DEFAULTS, units=["L-1"], partition=["alcohol", "tobacco"])
        self.assertTrue(two.hit.all())
        self.assertEqual(list(two.pick), [0, 0])
        self.assertFalse(two.taken.any())

    def test_unadmitted_object_is_neither_hit_nor_taken(self):
        s = scores([[0.90], [N]])
        sel = select(s, DEFAULTS, units=["L-1"])
        self.assertEqual(list(sel.hit), [True, False])
        self.assertEqual(list(sel.taken), [False, False])
        self.assertEqual(list(sel.rival), [-1, -1])

    def test_ties_go_to_the_lower_index(self):
        v = np.array([[0.5, 0.5],
                      [0.5, 0.5]])
        allowed = np.ones_like(v, dtype=bool)
        self.assertEqual(list(greedy_assign(v, allowed, ["U", "U"])), [0, -1])
        self.assertEqual(list(greedy_assign(v, allowed, ["U1", "U2"])), [0, 1])

    def test_units_length_is_checked(self):
        with self.assertRaises(ValueError):
            select(scores([[0.9, 0.8]]), DEFAULTS, units=["L-1"])


class TestEntities(unittest.TestCase):
    """A unit is claimed by an entity (a shop), not by a record of it."""

    def test_duplicate_records_share_the_unit(self):
        # One shop written twice (two files, two spellings).
        s = scores([[0.90],
                    [0.85]])
        sel = select(s, DEFAULTS, units=["L-1"], entities=["shop-1", "shop-1"])
        self.assertTrue(sel.hit.all())
        self.assertEqual(list(sel.pick), [0, 0])
        self.assertFalse(sel.taken.any())
        # Without entities the copy competes with itself and loses.
        plain = select(s, DEFAULTS, units=["L-1"])
        self.assertEqual(list(plain.taken), [False, True])

    def test_different_entities_still_compete(self):
        s = scores([[0.80],
                    [0.95],
                    [0.70]])
        sel = select(s, DEFAULTS, units=["L-1"],
                     entities=["shop-1", "shop-2", "shop-1"])
        self.assertEqual(list(sel.hit), [False, True, False])
        self.assertEqual(list(sel.taken), [True, False, True])
        self.assertEqual(list(sel.rival), [1, -1, 1])

    def test_rival_holds_the_unit_and_is_another_entity(self):
        s = scores([[0.95, 0.60],
                    [0.90, N],
                    [0.85, N],
                    [0.80, 0.70]])
        ent = ["e1", "e1", "e2", "e3"]
        units = ["L-1", "L-2"]
        sel = select(s, DEFAULTS, units=units, entities=ent)
        # e1 holds L-1 through both its records; e3 falls back to L-2; e2 is out.
        self.assertEqual(list(sel.hit), [True, True, False, True])
        self.assertEqual(list(sel.pick[[0, 1, 3]]), [0, 0, 1])
        for i in np.flatnonzero(sel.taken):
            r = int(sel.rival[i])
            self.assertGreaterEqual(r, 0)
            self.assertTrue(bool(sel.hit[r]))
            self.assertNotEqual(ent[r], ent[i])
            self.assertEqual(units[int(sel.pick[r])], units[int(sel.pick[i])])
        # And no unit is held by two entities.
        holders = {}
        for i in np.flatnonzero(sel.hit):
            holders.setdefault(units[int(sel.pick[i])], set()).add(ent[i])
        self.assertTrue(all(len(v) == 1 for v in holders.values()))

    def test_entities_respect_the_partition(self):
        s = scores([[0.90], [0.85], [0.80]])
        sel = select(s, DEFAULTS, units=["L-1"], entities=["e1", "e1", "e2"],
                     partition=["alcohol", "alcohol", "tobacco"])
        self.assertTrue(sel.hit.all())

    def test_entities_length_is_checked(self):
        with self.assertRaises(ValueError):
            select(scores([[0.9], [0.8]]), DEFAULTS, units=["L-1"], entities=["e1"])


class TestEligibility(unittest.TestCase):
    def test_ineligible_pair_counts_but_is_never_picked(self):
        s = scores([[0.90, 0.60]])
        sel = select(s, DEFAULTS, eligible=np.array([False, True]),
                     keys=["L-1", "L-2"], groups={"matched": None})
        self.assertEqual(int(sel.pick[0]), 1)
        self.assertTrue(bool(sel.hit[0]))
        self.assertEqual(int(sel.counts["matched"][0]), 2)
        self.assertFalse(bool(sel.blocked[0]))

    def test_nothing_eligible_is_blocked_not_unmatched(self):
        s = scores([[0.90, N],
                    [N, N]])
        sel = select(s, DEFAULTS, eligible=np.array([False, True]),
                     keys=["L-1", "L-2"], groups={"matched": None})
        self.assertEqual(list(sel.hit), [False, False])
        self.assertEqual(list(sel.blocked), [True, False])
        self.assertEqual(list(sel.taken), [False, False])
        self.assertEqual(list(sel.counts["matched"]), [1, 0])

    def test_pair_shaped_mask(self):
        s = scores([[0.90, 0.60],
                    [0.90, 0.60]])
        elig = np.array([[True, True],
                         [False, True]])
        sel = select(s, DEFAULTS, eligible=elig)
        self.assertEqual(list(sel.pick), [0, 1])

    def test_ineligible_pair_does_not_occupy_a_unit(self):
        # Object 0 is stronger at L-1 but may not take it; object 1 may.
        s = scores([[0.95],
                    [0.70]])
        elig = np.array([[False], [True]])
        sel = select(s, DEFAULTS, eligible=elig, units=["L-1"])
        self.assertEqual(list(sel.hit), [False, True])
        self.assertEqual(list(sel.blocked), [True, False])
        self.assertEqual(list(sel.taken), [False, False])

    def test_mask_shape_is_checked(self):
        with self.assertRaises(ValueError):
            select(scores([[0.9, 0.8]]), DEFAULTS, eligible=np.array([True]))


class TestPreferenceLevels(unittest.TestCase):
    # col 0: best score, no preference; col 1: level 1 only; col 2: level 0
    # only; col 3: both levels, worst score.
    S = scores([[0.90, 0.60, 0.50, 0.46]])
    LEVEL0 = np.array([False, False, True, True])
    LEVEL1 = np.array([False, True, False, True])

    def test_score_decides_without_preference(self):
        self.assertEqual(int(select(self.S, DEFAULTS).pick[0]), 0)

    def test_single_mask_and_one_tuple_are_the_same(self):
        self.assertEqual(int(select(self.S, DEFAULTS, prefer=self.LEVEL1).pick[0]), 1)
        self.assertEqual(int(select(self.S, DEFAULTS, prefer=(self.LEVEL1,)).pick[0]), 1)

    def test_level_zero_outranks_level_one(self):
        s = scores([[0.90, 0.60, 0.50]])
        sel = select(s, DEFAULTS, prefer=(self.LEVEL0[:3], self.LEVEL1[:3]))
        self.assertEqual(int(sel.pick[0]), 2)

    def test_both_levels_outrank_level_zero_alone(self):
        sel = select(self.S, DEFAULTS, prefer=(self.LEVEL0, self.LEVEL1))
        self.assertEqual(int(sel.pick[0]), 3)

    def test_levels_break_ties_by_score(self):
        s = scores([[0.60, 0.90]])
        sel = select(s, DEFAULTS, prefer=(np.array([True, True]),))
        self.assertEqual(int(sel.pick[0]), 1)

    def test_pair_shaped_preference(self):
        s = scores([[0.90, 0.60],
                    [0.90, 0.60]])
        pref = np.array([[False, True],
                         [False, False]])
        self.assertEqual(list(select(s, DEFAULTS, prefer=pref).pick), [1, 0])

    def test_preference_feeds_the_exclusive_assignment(self):
        # The in-force row wins the unit for the object that prefers it.
        s = scores([[0.90, 0.60],
                    [0.95, N]])
        sel = select(s, DEFAULTS, prefer=np.array([False, True]),
                     units=["U1", "U2"])
        self.assertEqual(list(sel.pick), [1, 0])


if __name__ == "__main__":
    unittest.main()
