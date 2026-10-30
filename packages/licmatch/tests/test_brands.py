"""Learning which company trades under which fascia, on small synthetic cities.

Each city is a list of objects (fascia, site) and a list of register outlets
(holder, site); an object and an outlet at the same site form an admitted pair
of strength 0.8 unless the test says otherwise.  Every city carries a
background of single-shop sites, each with its own holder, so that "how often
does this holder stand anywhere" -- the base rate -- is not trivially 1.
"""
from __future__ import annotations

import unittest

import numpy as np

from licmatch import BRAND_DEFAULTS, BrandParams, fascia_name, learn_brands


def background(n: int = 20):
    return ([(f"лавка {k}", f"bg{k}") for k in range(n)],
            [(f"BG{k}", f"bg{k}") for k in range(n)])


def city(objects, outlets, *, strength=0.8, override=None, bg=20):
    """(strength matrix, fascia keys, holders, sites) for learn_brands()."""
    bo, bh = background(bg)
    objects, outlets = list(objects) + bo, list(outlets) + bh
    m = np.full((len(objects), len(outlets)), np.nan)
    for i, (_, s) in enumerate(objects):
        for j, (_, t) in enumerate(outlets):
            if s == t:
                m[i, j] = strength
    for (i, j), v in (override or {}).items():
        m[i, j] = v
    return (m, [fascia_name(f) for f, _ in objects], [h for h, _ in outlets],
            [s for _, s in objects])


def learn(objects, outlets, params=BRAND_DEFAULTS, **kw):
    m, f, h, s = city(objects, outlets, **kw)
    return learn_brands(m, f, h, sites=s, params=params)


def spread(fascia, counts):
    """One fascia over consecutive sites, `n` of them run by each holder."""
    objects, outlets, k = [], [], 0
    for holder, n in counts:
        for _ in range(n):
            objects.append((fascia, f"s{k}"))
            outlets.append((holder, f"s{k}"))
            k += 1
    return objects, outlets


def row(result, fascia, holder):
    t = result.table
    hit = t[(t["fascia"] == fascia) & (t["holder"] == holder)]
    return None if hit.empty else hit.iloc[0]


class TestVoteAcrossAddresses(unittest.TestCase):
    """«Союз» at five addresses: R at every one, a different neighbour at each."""

    def setUp(self):
        objects = [("Союз", f"s{k}") for k in range(5)]
        outlets = ([("R", f"s{k}") for k in range(5)]
                   + [(f"N{k}", f"s{k}") for k in range(5)])
        # Two neighbours even out-score R on the pair itself; the vote must
        # still go to R.
        self.r = learn(objects, outlets, override={(0, 5): 0.85, (1, 6): 0.85})

    def test_the_real_holder_is_learned(self):
        self.assertIn(("союз", "R"), self.r.pairs)
        got = row(self.r, "союз", "R")
        self.assertEqual(int(got["sites_assigned"]), 5)
        self.assertEqual(float(got["share"]), 1.0)
        self.assertGreaterEqual(float(got["lift"]), BRAND_DEFAULTS.min_lift)
        self.assertEqual(int(got["holders_for_fascia"]), 1)

    def test_no_neighbour_is_learned(self):
        self.assertFalse({h for f, h in self.r.pairs if f == "союз"} - {"R"})
        self.assertEqual(self.r.pairs, frozenset({("союз", "R")}))

    def test_converges(self):
        self.assertTrue(self.r.converged)
        self.assertLess(self.r.iterations, BRAND_DEFAULTS.max_iter)

    def test_table_columns(self):
        for col in ("fascia", "holder", "sites_present", "sites_assigned", "share",
                    "cover", "base_rate", "lift", "accepted", "holders_for_fascia"):
            self.assertIn(col, self.r.table.columns)


class TestCompetitionForTheOutlet(unittest.TestCase):
    """A kiosk inside the supermarket at four addresses; one outlet at each.

    The supermarket stands at three more addresses of its own, and that is
    the only thing that tells the two apart: the pair strengths are equal.
    """

    def build(self, kiosk_first):
        objects = []
        for k in range(4):
            pair = [("Дымок, киоск", f"s{k}"), ("Пятёрочка", f"s{k}")]
            objects += pair if kiosk_first else pair[::-1]
        objects += [("Пятёрочка", f"s{k}") for k in range(4, 7)]
        outlets = [("A", f"s{k}") for k in range(7)]
        return learn(objects, outlets)

    def check(self, r):
        self.assertIn(("пятерочка", "A"), r.pairs)
        self.assertNotIn(("дымок", "A"), r.pairs)
        self.assertEqual(int(row(r, "пятерочка", "A")["sites_assigned"]), 7)
        self.assertIsNone(row(r, "дымок", "A"))   # never assigned: not in the table

    def test_supermarket_listed_first(self):
        self.check(self.build(kiosk_first=False))

    def test_kiosk_listed_first(self):
        # Regression: an unseeded first round handed the tie to the kiosk by
        # object order, and the kiosk then kept it on its own votes.
        self.check(self.build(kiosk_first=True))


class TestBaseRate(unittest.TestCase):
    def test_a_holder_standing_everywhere_fails_the_lift_test(self):
        # X has an outlet at 22 of the city's 25 sites; «Ромашка» stands at 5
        # of them with nobody else there, so it is assigned X every time.
        objects = [("Ромашка", f"s{k}") for k in range(5)]
        outlets = ([("X", f"s{k}") for k in range(5)]
                   + [("X", f"bg{k}") for k in range(17)])
        r = learn(objects, outlets)
        got = row(r, "ромашка", "X")
        self.assertEqual(int(got["sites_assigned"]), 5)
        self.assertEqual(float(got["share"]), 1.0)
        self.assertLess(float(got["lift"]), BRAND_DEFAULTS.min_lift)
        self.assertFalse(bool(got["accepted"]))
        self.assertNotIn(("ромашка", "X"), r.pairs)


class TestWhatIsNeverLearned(unittest.TestCase):
    def test_generic_fascia_never_appears(self):
        objects = [("Продукты", f"s{k}") for k in range(5)]
        outlets = [("G", f"s{k}") for k in range(5)]
        r = learn(objects, outlets)
        self.assertNotIn("продукты", set(r.table["fascia"]))
        self.assertFalse({f for f, _ in r.pairs} & {"продукты", ""})

    def test_fascia_reducing_to_nothing_never_appears(self):
        objects = [("Кафе", f"s{k}") for k in range(5)]
        outlets = [("G", f"s{k}") for k in range(5)]
        r = learn(objects, outlets)
        self.assertNotIn("", set(r.table["fascia"]))
        self.assertEqual(r.pairs, frozenset())

    def test_too_few_addresses(self):
        objects = [("Ладья", f"s{k}") for k in range(2)]
        outlets = [("L", f"s{k}") for k in range(2)]
        r = learn(objects, outlets)
        got = row(r, "ладья", "L")
        self.assertEqual(int(got["sites_assigned"]), 2)
        self.assertGreaterEqual(float(got["lift"]), BRAND_DEFAULTS.min_lift)
        self.assertFalse(bool(got["accepted"]))
        # The same evidence clears a lower bar, so it is the count that failed.
        loose = learn(objects, outlets, params=BrandParams(min_sites=2))
        self.assertIn(("ладья", "L"), loose.pairs)

    def test_two_records_of_one_shop_vote_once(self):
        objects = [("Ладья", "s0"), ("Ладья", "s0"), ("Ладья", "s1")]
        outlets = [("L", "s0"), ("L", "s0"), ("L", "s1")]
        r = learn(objects, outlets)
        self.assertEqual(int(row(r, "ладья", "L")["sites_assigned"]), 2)
        self.assertNotIn(("ладья", "L"), r.pairs)


class TestFranchise(unittest.TestCase):
    """A fascia several operators share, against one a single holder dominates.

    Each site has exactly one outlet, so shares are fixed by construction.  A
    larger background keeps a holder confined to one fascia above the lift bar.
    """

    def test_every_operator_of_a_franchise_is_learned(self):
        # Top holder 4/12 = 0.33 < franchise_top_max: a franchise.  P2 and P3
        # at 0.25 miss min_share but clear franchise_min_share.
        objects, outlets = spread("Петровский",
                                       [("P1", 4), ("P2", 3), ("P3", 3), ("P4", 2)])
        r = learn(objects, outlets, bg=40)
        self.assertTrue(r.table.loc[r.table["fascia"] == "петровский", "franchise"].all())
        self.assertEqual({h for f, h in r.pairs if f == "петровский"}, {"P1", "P2", "P3"})
        # P4 has the share and the lift; two addresses are still too few.
        self.assertFalse(bool(row(r, "петровский", "P4")["accepted"]))
        self.assertEqual(int(row(r, "петровский", "P1")["holders_for_fascia"]), 3)

    def test_without_the_rule_only_the_top_operator_survives(self):
        objects, outlets = spread("Петровский",
                                       [("P1", 4), ("P2", 3), ("P3", 3), ("P4", 2)])
        r = learn(objects, outlets, bg=40, params=BrandParams(franchise_top_max=0.0))
        self.assertFalse(r.table["franchise"].any())
        self.assertEqual({h for f, h in r.pairs if f == "петровский"}, {"P1"})

    def test_a_dominated_fascia_keeps_the_strict_bar(self):
        # H1 holds 9/13 = 0.69: not a franchise, so H2 at 3/13 = 0.23 is refused
        # although it has three addresses and the lift.
        objects, outlets = spread("Северянка", [("H1", 9), ("H2", 3), ("H3", 1)])
        r = learn(objects, outlets, bg=40)
        h2 = row(r, "северянка", "H2")
        self.assertFalse(bool(h2["franchise"]))
        self.assertEqual(int(h2["sites_assigned"]), 3)
        self.assertGreaterEqual(float(h2["lift"]), BRAND_DEFAULTS.min_lift)
        self.assertLess(float(h2["share"]), BRAND_DEFAULTS.min_share)
        self.assertGreaterEqual(float(h2["share"]), BRAND_DEFAULTS.franchise_min_share)
        self.assertFalse(bool(h2["accepted"]))
        self.assertEqual({h for f, h in r.pairs if f == "северянка"}, {"H1"})


class TestHolderCover(unittest.TestCase):
    """The franchise rule's second half: the test from the holder's side."""

    SPREAD = [("P1", 4), ("P2", 3), ("P3", 3), ("P4", 2)]

    def build(self, extra_p2=0, params=BRAND_DEFAULTS):
        objects, outlets = spread("Петровский", self.SPREAD)
        # P2 also runs outlets at background addresses other shops stand at.
        outlets += [("P2", f"bg{k}") for k in range(extra_p2)]
        return learn(objects, outlets, bg=80, params=params)

    def test_operators_confined_to_the_franchise_pass(self):
        r = self.build()
        for h in ("P1", "P2", "P3"):
            self.assertEqual(float(row(r, "петровский", h)["holder_cover"]), 1.0)
        self.assertIn(("петровский", "P2"), r.pairs)

    def test_a_holder_mostly_elsewhere_is_refused(self):
        # 3 of P2's 7 reached outlets stand at the fascia: 0.43 < 0.5.  Share,
        # site count and lift would all admit it.
        r = self.build(extra_p2=4)
        p2 = row(r, "петровский", "P2")
        self.assertTrue(bool(p2["franchise"]))
        self.assertAlmostEqual(float(p2["holder_cover"]), 3 / 7, places=3)
        self.assertGreaterEqual(float(p2["share"]), BRAND_DEFAULTS.franchise_min_share)
        self.assertGreaterEqual(float(p2["lift"]), BRAND_DEFAULTS.min_lift)
        self.assertGreaterEqual(int(p2["sites_assigned"]), BRAND_DEFAULTS.min_sites)
        self.assertFalse(bool(p2["accepted"]))
        self.assertIn(("петровский", "P3"), r.pairs)

    def test_the_cover_is_what_refused_it(self):
        r = self.build(extra_p2=4, params=BrandParams(franchise_holder_cover=0.0))
        self.assertIn(("петровский", "P2"), r.pairs)

    def test_cover_does_not_bind_above_min_share(self):
        # P1 at 4/12 = 0.33 clears min_share outright; its cover is not asked.
        objects, outlets = spread("Петровский", self.SPREAD)
        outlets += [("P1", f"bg{k}") for k in range(5)]
        r = learn(objects, outlets, bg=80)
        p1 = row(r, "петровский", "P1")
        self.assertLess(float(p1["holder_cover"]), BRAND_DEFAULTS.franchise_holder_cover)
        self.assertTrue(bool(p1["accepted"]))


class TestLeaveOneSiteOut(unittest.TestCase):
    def learn_n(self, n):
        return learn([("Союз", f"s{k}") for k in range(n)],
                     [("R", f"s{k}") for k in range(n)])

    def test_pair_at_exactly_min_sites_is_not_loo_accepted(self):
        r = self.learn_n(BRAND_DEFAULTS.min_sites)
        got = row(r, "союз", "R")
        self.assertTrue(bool(got["accepted"]))
        self.assertFalse(bool(got["loo_accepted"]))

    def test_one_more_site_is_both(self):
        r = self.learn_n(BRAND_DEFAULTS.min_sites + 1)
        got = row(r, "союз", "R")
        self.assertTrue(bool(got["accepted"]))
        self.assertTrue(bool(got["loo_accepted"]))

    def test_loo_never_exceeds_acceptance(self):
        r = learn([("Союз", f"s{k}") for k in range(5)] + [("Ладья", "t0"), ("Ладья", "t1")],
                  [("R", f"s{k}") for k in range(5)] + [("L", "t0"), ("L", "t1")])
        t = r.table
        self.assertFalse((t["loo_accepted"] & ~t["accepted"]).any())


class TestSiteVotes(unittest.TestCase):
    def test_votes_are_the_assigned_sites(self):
        objects = [("Союз", f"s{k}") for k in range(4)] + [("Союз", "s0")]
        outlets = [("R", f"s{k}") for k in range(4)] + [("N0", "s3")]
        r = learn(objects, outlets)
        self.assertEqual(r.site_votes[("союз", "R")],
                         frozenset({"s0", "s1", "s2", "s3"}))
        self.assertNotIn(("союз", "N0"), r.site_votes)
        # One entry per pair with votes, and the count matches the table.
        for (f, h), sites in r.site_votes.items():
            got = row(r, f, h)
            if got is not None:
                self.assertEqual(int(got["sites_assigned"]), len(sites))

    def test_generic_fascias_have_no_votes(self):
        r = learn([("Продукты", f"s{k}") for k in range(4)],
                  [("G", f"s{k}") for k in range(4)])
        self.assertFalse(any(f == "продукты" for f, _ in r.site_votes))

    def test_oscillating_objects_leave_no_votes(self):
        r = TestTwoCycle.build_cycle()
        self.assertFalse(any(f in ("альфа", "бета") for f, _ in r.site_votes))
        self.assertEqual(r.site_votes[("союз", "R")], frozenset({"u0", "u1", "u2"}))


class TestTwoCycle(unittest.TestCase):
    """Two fascias sharing one outlet at each of two addresses.

    Whoever wins an address hands the *other* address its prior (the prior
    never counts the object's own site), so the two swap every round: F1 takes
    s1 and F2 s2, then F2 s1 and F1 s2, then back.  Neither state is an answer.
    """

    @staticmethod
    def build_cycle():
        objects = [("Альфа", "s1"), ("Бета", "s1"), ("Альфа", "s2"), ("Бета", "s2"),
                   ("Союз", "u0"), ("Союз", "u1"), ("Союз", "u2")]
        outlets = [("H", "s1"), ("H", "s2"), ("R", "u0"), ("R", "u1"), ("R", "u2")]
        # Round one is decided by the pair strengths: Альфа at s1, Бета at s2.
        return learn(objects, outlets, override={(0, 0): 0.81, (3, 1): 0.81})

    def setUp(self):
        self.r = self.build_cycle()

    def test_the_cycle_is_reported(self):
        self.assertEqual(self.r.cycle, 2)
        self.assertFalse(self.r.converged)
        self.assertEqual(self.r.oscillating, 4)

    def test_oscillating_objects_vote_for_nothing(self):
        fascias = set(self.r.table["fascia"])
        self.assertFalse(fascias & {"альфа", "бета"})
        self.assertFalse({f for f, _ in self.r.pairs} & {"альфа", "бета"})

    def test_stable_objects_keep_their_votes(self):
        self.assertIn(("союз", "R"), self.r.pairs)
        self.assertEqual(int(row(self.r, "союз", "R")["sites_assigned"]), 3)

    def test_a_settled_run_reports_no_cycle(self):
        r = learn([("Союз", f"s{k}") for k in range(3)], [("R", f"s{k}") for k in range(3)])
        self.assertEqual((r.cycle, r.oscillating, r.converged), (0, 0, True))

    def test_running_out_of_rounds_is_reported(self):
        r = learn([("Союз", f"s{k}") for k in range(3)], [("R", f"s{k}") for k in range(3)],
                  params=BrandParams(max_iter=1))
        self.assertEqual(r.cycle, -1)
        self.assertFalse(r.converged)


class TestFasciaKey(unittest.TestCase):
    def test_ampersand_spellings_count_as_one_fascia(self):
        objects = [("Красное&Белое", "s0"), ("Красное & Белое", "s1"),
                   ("Красное&Белое", "s2")]
        outlets = [("K", f"s{k}") for k in range(3)]
        r = learn(objects, outlets)
        self.assertIn(("красное белое", "K"), r.pairs)

    def test_nothing_admitted_gives_an_empty_table(self):
        r = learn_brands(np.full((2, 2), np.nan), ["союз", "дымок"], ["A", "B"])
        self.assertEqual(len(r.table), 0)
        self.assertIn("franchise", r.table.columns)
        self.assertEqual(r.pairs, frozenset())
        self.assertEqual(list(r.assignment), [-1, -1])

    def test_shape_mismatch_is_an_error(self):
        m, f, h, s = city([("Союз", "s0")], [("R", "s0")])
        with self.assertRaises(ValueError):
            learn_brands(m, f[:-1], h, sites=s[:-1])


if __name__ == "__main__":
    unittest.main()
