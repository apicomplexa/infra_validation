"""The two keys a name is reduced to, and which names say nothing at all."""
from __future__ import annotations

import unittest

from licmatch import (GENERIC_FASCIAS, brand_key, fascia_name, is_generic,
                      name_key)


class TestBrandKey(unittest.TestCase):
    def test_ampersand_spellings_meet(self):
        # name_key keeps "&" inside a token and drops it standing alone, so the
        # two spellings reach different name keys -- and one brand key.
        joined, spaced = fascia_name("Красное&Белое"), fascia_name("Красное & Белое")
        self.assertNotEqual(joined, spaced)
        self.assertEqual(brand_key(joined), "красное белое")
        self.assertEqual(brand_key(spaced), "красное белое")

    def test_plain_key_is_unchanged(self):
        self.assertEqual(brand_key("пятерочка"), "пятерочка")
        self.assertEqual(brand_key("  fix   price "), "fix price")

    def test_name_key_itself_is_not_folded(self):
        # Scoring reads name_key; only brand learning and alias lookup fold.
        self.assertEqual(name_key("красное&белое"), "красное&белое")


class TestGeneric(unittest.TestCase):
    def test_kind_of_shop_is_generic(self):
        for raw in ("Продукты", "Продукты 24", "Алкомаркет", "Разливное пиво",
                    "Табак"):
            with self.subTest(raw=raw):
                self.assertTrue(is_generic(fascia_name(raw)))

    def test_bare_trade_descriptor_reduces_to_empty_and_is_generic(self):
        for raw in ("Кафе", "Бар", "Магазин", "Киоск"):
            with self.subTest(raw=raw):
                self.assertEqual(fascia_name(raw), "")
                self.assertTrue(is_generic(fascia_name(raw)))

    def test_a_shop_name_is_not_generic(self):
        for raw in ("Пятёрочка, супермаркет", "Красное&Белое", "Союз", "Дымок"):
            with self.subTest(raw=raw):
                self.assertFalse(is_generic(fascia_name(raw)))

    def test_list_holds_brand_keys(self):
        # is_generic() looks the brand key up; an entry that is not one would
        # be unreachable.
        for key in GENERIC_FASCIAS:
            self.assertEqual(brand_key(key), key)


if __name__ == "__main__":
    unittest.main()
