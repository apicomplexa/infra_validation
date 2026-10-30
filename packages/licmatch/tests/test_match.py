"""`match()`: the five stages, one condition or rule at a time.

Every fixture is a handful of objects and register rows on real Arkhangelsk
street names, so the toponym and scoring layers run as they do in production;
what a test varies is the one thing it is about -- a holder's name, a status,
a kind, a coordinate -- and it asserts the status and the named condition the
explanation gives, never a score.
"""
from __future__ import annotations

import unittest

import licmatch as lm
import numpy as np
import pandas as pd

LAT, LON = 64.5400, 40.5400
RETAIL, CATERING = "retail", "catering"


def objects(*rows) -> pd.DataFrame:
    """(name, address, dlat_m, dlon_m[, extra dict]) -> an object table."""
    out = []
    for r in rows:
        name, addr, dn, de = r[:4]
        d = {"name": name, "address": addr, "lat": LAT + dn / 111_000, "lon": LON + de / 48_000}
        d.update(r[4] if len(r) > 4 else {})
        out.append(d)
    df = pd.DataFrame(out)
    df.index = pd.Index([f"O{i}" for i in range(len(df))], name="object_id")
    return df


def register(*rows) -> pd.DataFrame:
    """dicts with holder, street, house and optional overrides -> a RegisterTable."""
    out = []
    for i, r in enumerate(rows):
        d = {"record_id": f"L-{i}", "holder_id": f"INN{i}", "holder_name": r["holder"],
             "venue_name": "", "address_line": f"{r['street']}, {r['house']}",
             "street": r["street"], "house": r["house"], "settlement": "", "block": "",
             "lat": LAT + r.get("dn", 0.0) / 111_000, "lon": LON + r.get("de", 0.0) / 48_000,
             "in_force": True, "kind": RETAIL}
        d.update({k: v for k, v in r.items() if k not in ("holder", "street", "house", "dn", "de")})
        out.append(d)
    return pd.DataFrame(out)


BASE = dict(home_cities=("Архангельск",))


def policy(**kw) -> lm.Policy:
    return lm.Policy(**{**BASE, **kw})


class TestConditions(unittest.TestCase):
    def test_admission_is_derived_from_the_fields(self):
        self.assertEqual(policy().conditions(), (lm.C_ADDRESS, lm.C_KIND))
        strict = policy(require_in_force=True, name_policy="required")
        self.assertEqual(strict.conditions(), (lm.C_ADDRESS, lm.C_KIND, lm.C_IN_FORCE, lm.C_NAME))
        v4 = policy(kinds=lm.Kinds(holder_override=0.8), brands=lm.Brands(mode="known", conflict=True),
                    archived=lm.Archived())
        self.assertEqual(v4.conditions(), (lm.C_ADDRESS, lm.C_KIND_OR_HOLDER, lm.C_BRAND, lm.C_ARCHIVED))

    def test_unknown_names_are_refused(self):
        with self.assertRaises(ValueError):
            policy(explain=(lm.Rung(lm.OTHER_HOLDER, ("adress",)),))
        with self.assertRaises(ValueError):
            policy(name_policy="always")
        with self.assertRaises(ValueError):
            policy(exclusive=lm.Exclusive(share="entity"))   # needs Policy.entities


class TestAddressAndPurity(unittest.TestCase):
    ob = objects(("Ромашка", "улица Тимме, 2", 0, 0), ("Ромашка", "улица Тимме, 4", 0, 0))
    reg = register({"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2"})

    def test_house_number_decides(self):
        t = lm.match(self.ob, self.reg, policy()).table
        self.assertEqual(list(t["status"]), [lm.OWN, lm.NONE])
        self.assertEqual(t.loc["O0", "record"], 0)
        self.assertEqual(t.loc["O0", "record_id"], "L-0")
        self.assertTrue(t.loc["O0", "assigned"])
        self.assertEqual(list(t.index), ["O0", "O1"])

    def test_pure(self):
        before_ob, before_reg = self.ob.copy(), self.reg.copy()
        a = lm.match(self.ob, self.reg, policy()).table
        b = lm.match(self.ob, self.reg, policy()).table
        pd.testing.assert_frame_equal(a, b)
        pd.testing.assert_frame_equal(self.ob, before_ob)
        pd.testing.assert_frame_equal(self.reg, before_reg)

    def test_empty_register(self):
        t = lm.match(self.ob, self.reg.iloc[:0], policy()).table
        self.assertEqual(set(t["status"]), {lm.NONE})


class TestExplanation(unittest.TestCase):
    """The catering ladder, rung by rung."""
    LADDER = (
        lm.Rung(lm.OTHER_HOLDER, (lm.C_ADDRESS, lm.C_KIND, lm.C_IN_FORCE)),
        lm.Rung(lm.INACTIVE, (lm.C_ADDRESS, lm.C_KIND, lm.C_NAME)),
        lm.Rung(lm.ELSEWHERE, (lm.C_NEAR, lm.C_KIND, lm.C_IN_FORCE, lm.C_NAME), forbid=(lm.C_ADDRESS,)),
        lm.Rung(lm.OTHER_HOLDER, (lm.C_ADDRESS, lm.C_KIND)),
        lm.Rung(lm.WRONG_KIND, (lm.C_ADDRESS, lm.C_WRONG_KIND, lm.C_IN_FORCE)),
    )

    def strict(self, **kw):
        return policy(kinds=lm.Kinds(allowed=frozenset({CATERING}), wrong=frozenset({RETAIL})),
                      require_in_force=True, name_policy="required",
                      name_fields=("holder", "venue"), explain=self.LADDER, **kw)

    def one(self, reg, name="Жасмин", addr="улица Тимме, 2"):
        return lm.match(objects((name, addr, 0, 0)), reg, self.strict()).table.iloc[0]

    def test_own_by_holder(self):
        r = self.one(register({"holder": 'ООО "Жасмин"', "street": "Тимме ул", "house": "2",
                               "kind": CATERING}))
        self.assertEqual((r["status"], r["name_via"]), (lm.OWN, "holder"))

    def test_own_by_venue(self):
        r = self.one(register({"holder": 'ООО "Бермуда"', "venue_name": "Жасмин",
                               "street": "Тимме ул", "house": "2", "kind": CATERING}))
        self.assertEqual((r["status"], r["name_via"]), (lm.OWN, "venue"))

    def test_other_holder_fails_name(self):
        r = self.one(register({"holder": 'ООО "Бермуда"', "street": "Тимме ул", "house": "2",
                               "kind": CATERING}))
        self.assertEqual((r["status"], r["failed_rule"], r["near_miss"]),
                         (lm.OTHER_HOLDER, lm.C_NAME, True))

    def test_generic_fascia_never_agrees(self):
        r = self.one(register({"holder": 'ООО "Кафе"', "street": "Тимме ул", "house": "2",
                               "kind": CATERING}), name="Кафе")
        self.assertEqual(r["status"], lm.OTHER_HOLDER)
        self.assertTrue(r["generic"])

    def test_lapsed_own_name(self):
        r = self.one(register({"holder": 'ООО "Жасмин"', "street": "Тимме ул", "house": "2",
                               "kind": CATERING, "in_force": False}))
        self.assertEqual((r["status"], r["failed_rule"], r["assigned"]),
                         (lm.INACTIVE, lm.C_IN_FORCE, False))

    def test_elsewhere(self):
        r = self.one(register({"holder": 'ООО "Жасмин"', "street": "Тимме ул", "house": "8",
                               "kind": CATERING, "dn": 40}))
        self.assertEqual((r["status"], r["failed_rule"]), (lm.ELSEWHERE, lm.C_ADDRESS))
        self.assertEqual(r["method"], "geo")

    def test_lapsed_other_holder(self):
        r = self.one(register({"holder": 'ООО "Бермуда"', "street": "Тимме ул", "house": "2",
                               "kind": CATERING, "in_force": False}))
        self.assertEqual((r["status"], r["failed_rule"]),
                         (lm.OTHER_HOLDER, f"{lm.C_IN_FORCE}+{lm.C_NAME}"))

    def test_retail_only(self):
        r = self.one(register({"holder": 'ООО "Жасмин"', "street": "Тимме ул", "house": "2"}))
        self.assertEqual((r["status"], r["failed_rule"]), (lm.WRONG_KIND, lm.C_KIND))
        r = self.one(register({"holder": 'ООО "Бермуда"', "street": "Тимме ул", "house": "2"}))
        self.assertEqual((r["status"], r["failed_rule"]), (lm.WRONG_KIND, f"{lm.C_KIND}+{lm.C_NAME}"))

    def test_rung_order_decides(self):
        # Another holder's live licence outranks the object's own lapsed one.
        r = self.one(register(
            {"holder": 'ООО "Жасмин"', "street": "Тимме ул", "house": "2", "kind": CATERING,
             "in_force": False},
            {"holder": 'ООО "Бермуда"', "street": "Тимме ул", "house": "2", "kind": CATERING}))
        self.assertEqual((r["status"], r["record"]), (lm.OTHER_HOLDER, 1))

    def test_second_address_of_a_corner(self):
        reg = register({"holder": 'ООО "Жасмин"', "street": "Северной Двины наб", "house": "61",
                        "kind": CATERING})
        ob = objects(("Жасмин", "Троицкий проспект, 17", 0, 0,
                      {"address_2": "набережная Северной Двины, 61"}))
        self.assertEqual(lm.match(ob, reg, self.strict()).table.iloc[0]["status"], lm.OWN)
        ob = ob.drop(columns="address_2")
        self.assertNotEqual(lm.match(ob, reg, self.strict()).table.iloc[0]["status"], lm.OWN)


class TestAssignment(unittest.TestCase):
    reg = register({"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2"},
                   {"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "holder_id": "INN9"})

    def test_without_exclusivity_everyone_quotes_the_best(self):
        ob = objects(("Ромашка", "улица Тимме, 2", 0, 0), ("Лютик", "улица Тимме, 2", 30, 0))
        t = lm.match(ob, self.reg.iloc[:1], policy()).table
        self.assertEqual(list(t["status"]), [lm.OWN, lm.OWN])

    def test_fallback_and_taken(self):
        ob = objects(("Ромашка", "улица Тимме, 2", 0, 0), ("Лютик", "улица Тимме, 2", 30, 0),
                     ("Василёк", "улица Тимме, 2", 60, 0))
        p = policy(exclusive=lm.Exclusive(share="name", fallback=True))
        t = lm.match(ob, self.reg, p).table
        # Two units (two INNs): the strongest claim takes one, the next the
        # other, the third is left with nothing.
        self.assertEqual(list(t["status"]), [lm.OWN, lm.OWN, lm.TAKEN])
        self.assertEqual(t.loc["O2", "failed_rule"], lm.C_EXCLUSIVE)
        self.assertIn(t.loc["O2", "claimed_by"], ("O0", "O1"))
        self.assertGreaterEqual(t.loc["O2", "margin"], 0)
        p = policy(exclusive=lm.Exclusive(share="name", fallback=False))
        t = lm.match(ob, self.reg, p).table
        # Without fallback a loser does not move on to the second unit.
        self.assertEqual(list(t["status"]), [lm.OWN, lm.TAKEN, lm.TAKEN])
        self.assertEqual(list(t["claimed_by"][1:]), ["O0", "O0"])

    def test_share_by_name(self):
        ob = objects(("Престо", "улица Тимме, 2", 0, 0), ("Престо, ресторан-кафе", "улица Тимме, 2", 5, 0),
                     ("Лютик", "улица Тимме, 2", 10, 0))
        p = policy(exclusive=lm.Exclusive(share="name", fallback=False))
        t = lm.match(ob, self.reg.iloc[:1], p).table
        self.assertEqual(list(t["status"]), [lm.OWN, lm.OWN, lm.TAKEN])

    def test_share_by_entity_and_rival(self):
        ob = objects(("Ромашка", "улица Тимме, 2", 0, 0), ("Ромашка, магазин", "улица Тимме, 2", 3, 0),
                     ("Лютик", "улица Тимме, 2", 80, 0))
        ent = lm.Entities()
        claimant = policy(entities=ent, exclusive=lm.Exclusive(share="entity"))
        last = policy(entities=ent, exclusive=lm.Exclusive(share="entity", rival="last"))
        t = lm.match(ob, self.reg.iloc[:1], claimant).table
        self.assertEqual(list(t["status"]), [lm.OWN, lm.OWN, lm.TAKEN])
        self.assertEqual(t.loc["O0", "entity"], t.loc["O1", "entity"])
        self.assertEqual(t.loc["O2", "claimed_by"], "O0")
        self.assertEqual(lm.match(ob, self.reg.iloc[:1], last).table.loc["O2", "claimed_by"], "O1")

    def test_prefer_outranks_score(self):
        reg = register({"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "in_force": False},
                       {"holder": 'ООО "Другое"', "street": "Тимме ул", "house": "2", "dn": 60})
        ob = objects(("Ромашка", "улица Тимме, 2", 0, 0))
        self.assertEqual(lm.match(ob, reg, policy()).table.iloc[0]["record"], 0)
        t = lm.match(ob, reg, policy(prefer=(lm.C_IN_FORCE,))).table.iloc[0]
        self.assertEqual((t["record"], t["status"]), (1, lm.OWN))


class TestKindsBrandsArchived(unittest.TestCase):
    def test_kind_by_column_and_holder_override(self):
        reg = register({"holder": 'ООО "Луда-Старт"', "street": "Нагорная ул", "house": "3", "kind": CATERING})
        ob = objects(("Луда", "Нагорная улица, 3", 0, 0, {"trade": "off"}),
                     ("Мираж", "Нагорная улица, 3", 0, 0, {"trade": "off"}))
        kinds = lm.Kinds(column="trade", by_value={"off": frozenset({RETAIL})})
        rungs = (lm.Rung(lm.WRONG_KIND, (lm.C_ADDRESS,)),)
        t = lm.match(ob, reg, policy(kinds=kinds, explain=rungs)).table
        self.assertEqual(list(t["status"]), [lm.WRONG_KIND, lm.WRONG_KIND])
        kinds = lm.Kinds(column="trade", by_value={"off": frozenset({RETAIL})}, holder_override=0.80)
        t = lm.match(ob, reg, policy(kinds=kinds, explain=rungs)).table
        # The name says the holder is the shop: past the kind rule, kind_match stays False.
        self.assertEqual(list(t["status"]), [lm.OWN, lm.WRONG_KIND])
        self.assertFalse(t.loc["O0", "kind_match"])

    def test_known_chain_both_ways(self):
        reg = register({"holder": 'АО "Тандер"', "street": "Тимме ул", "house": "2"})
        ob = objects(("Пятёрочка", "улица Тимме, 2", 0, 0), ("Магнит", "улица Тимме, 2", 0, 0),
                     ("Пивная лавка", "улица Тимме, 2", 0, 0))
        rungs = (lm.Rung(lm.OTHER_CHAIN, (lm.C_ADDRESS, lm.C_KIND)),)
        p = policy(brands=lm.Brands(mode="known", conflict=True), explain=rungs)
        t = lm.match(ob, reg, p).table
        self.assertEqual(list(t["status"]), [lm.OTHER_CHAIN, lm.OWN, lm.OTHER_CHAIN])
        self.assertEqual(t.loc["O1", "name_basis"], "manual")
        self.assertEqual(t.loc["O0", "failed_rule"], lm.C_BRAND)
        p = policy(brands=lm.Brands(mode="known", conflict=False), explain=rungs)
        self.assertEqual(set(lm.match(ob, reg, p).table["status"]), {lm.OWN})

    def test_archived_belongs_to_previous_tenant(self):
        reg = register({"holder": 'ООО "Южанка"', "street": "Тимме ул", "house": "2", "in_force": False},
                       {"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "dn": 90})
        ob = objects(("Пятёрочка", "улица Тимме, 2", 0, 0))
        rungs = (lm.Rung(lm.OTHER_CHAIN, (lm.C_ADDRESS, lm.C_KIND)),)
        p = policy(archived=lm.Archived(), explain=rungs)
        t = lm.match(ob, reg, p).table.iloc[0]
        # The live licence is held; the archived one of another name is not.
        self.assertEqual((t["status"], t["record"]), (lm.OWN, 1))
        t = lm.match(ob, reg.iloc[:1], p).table.iloc[0]
        # Nothing live of its kind at the address: the archived licence stands.
        self.assertEqual((t["status"], t["assigned"]), (lm.INACTIVE, True))


class TestBlocksAndTallies(unittest.TestCase):
    def test_blocks(self):
        reg = register({"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "city": "a"},
                       {"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "city": "b"})
        ob = objects(("Ромашка", "улица Тимме, 2", 0, 0, {"city": "b"}),
                     ("Ромашка", "улица Тимме, 2", 0, 0, {"city": ""}),
                     ("Ромашка", "улица Тимме, 2", 0, 0, {"city": "a"}))
        t = lm.match(ob, reg, policy(block="city")).table
        self.assertEqual(list(t["status"]), [lm.OWN, lm.NONE, lm.OWN])
        self.assertEqual(list(t["record"]), [1, -1, 0])

    def test_distinct_ids(self):
        reg = register({"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "record_id": "L-1"},
                       {"holder": 'ООО "Ромашка"', "street": "Тимме ул", "house": "2", "record_id": "L-1",
                        "in_force": False},
                       {"holder": 'ООО "Другое"', "street": "Тимме ул", "house": "2", "record_id": "L-2",
                        "kind": CATERING})
        ob = objects(("Ромашка", "улица Тимме, 2", 0, 0))
        p = policy(tallies={"all": lm.Tally(), "live": lm.Tally(in_force=True),
                            "cat": lm.Tally(kinds=frozenset({CATERING}))})
        t = lm.match(ob, reg, p).table.iloc[0]
        self.assertEqual((t["n_all"], t["n_live"], t["n_cat"]), (2, 2, 1))
        self.assertEqual(t["ids_all"], "L-1; L-2")


class TestNamesAgree(unittest.TestCase):
    def test_words_not_partial_ratios(self):
        self.assertFalse(lm.names_agree("миг", "мигрант"))
        self.assertTrue(lm.names_agree("мясо", "гастробар мясо"))
        self.assertTrue(lm.names_agree("холмс", "холмс фуд"))

    def test_two_scripts(self):
        self.assertTrue(lm.names_agree("el fuego", "эль фуэго"))

    def test_generic_and_place_words(self):
        self.assertFalse(lm.names_agree("кофе", "кофе хаус"))
        self.assertTrue(lm.is_generic_name("пицца"))
        self.assertTrue(lm.names_agree("двина", "двина"))
        self.assertTrue(lm.names_agree("северное сияние", "север плюс") is False)
        place = frozenset({"двина"})
        self.assertFalse(lm.names_agree("двина кафе-бар", "двина сервис", place))
        self.assertTrue(lm.names_agree("двина кафе-бар", "двина сервис"))

    def test_matrix(self):
        m = lm.agree_matrix(np.array(["холмс", "кофе"], dtype=object),
                            np.array(["холмс фуд", "кофе хаус", "холмс"], dtype=object))
        self.assertEqual(m.tolist(), [[True, False, True], [False, False, False]])


if __name__ == "__main__":
    unittest.main()
