import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from infracheck.audit.verify import (
    Check,
    URLVerifier,
    evidence_day,
    is_primary,
    label_strength,
    object_toponyms,
    source_type,
    verify_label,
)

ROOT = Path(__file__).resolve().parents[2]
URL_CACHE = ROOT / "resources" / "cache" / "audit" / "urls"
FILLER = "Нейтральный контекст страницы. " * 30


def page(body, title=""):
    head = f"<head><title>{title}</title></head>" if title else ""
    return f"<html>{head}<body>{FILLER}{body}</body></html>"


def verifier_for(pages, tmp_path):
    session = Mock()
    session.get.side_effect = lambda url, **kwargs: Mock(text=pages[url])
    return URLVerifier(tmp_path, session)


def test_fetch_cache_and_page_text(tmp_path):
    response = Mock(text="<html><body>Кафе Ромашка работает</body></html>")
    session = Mock()
    session.get.return_value = response
    verifier = URLVerifier(tmp_path, session)
    # Short pages are JS-only/unreachable under the verification rules.
    assert verifier.verify("https://example.org/", "Ромашка", "") == "unreachable"
    assert session.get.call_args.kwargs["headers"]["Accept-Language"].startswith("ru")
    assert "Firefox" in session.get.call_args.kwargs["headers"]["User-Agent"]
    assert not list(tmp_path.iterdir())
    session.get.assert_called_once()
    assert session.get.call_args.kwargs["timeout"] == 15


def test_offline_verifier_never_requests(tmp_path):
    session = Mock()
    verifier = URLVerifier(tmp_path, session, offline=True)
    assert verifier.verify("https://example.org/", "Ромашка", "") == "unreachable"
    session.get.assert_not_called()


def test_unreachable_is_not_a_contradiction_but_is_weak(tmp_path):
    session = Mock()
    session.get.side_effect = requests.Timeout()
    verifier = URLVerifier(tmp_path, session)
    label = {"exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no", "urls": ["https://2gis.ru/a"]}
    result = verify_label(label, "Название", verifier, "Архангельск", "Гагарина, 1")
    assert result["n_unreachable"] == 1
    assert result["url_types"] == ["2gis"]
    assert "Не проверены источники" not in result.get("notes", "")
    assert result["category_ok"] == "yes"
    assert (result["exists_raw"], result["open_raw"]) == ("yes", "open")
    assert result["exists"] == result["open"] == "unclear"
    assert result["label_strength"] == "weak"
    assert not list(tmp_path.iterdir())
    assert label["exists"] == "yes"


def test_source_domain_is_not_substring():
    assert source_type("https://maps.yandex.ru/x") == "yandex"
    assert source_type("https://yandex.ru.evil.test/x") == "other"


def test_every_citation_is_checked():
    verifier = Mock()
    verifier.check.side_effect = [Check("verified"), Check("unreachable")]
    result = verify_label(
        {"exists": "yes", "urls": ["https://2gis.ru/arkhangelsk/firm/1", "https://example.org/b"]}, "Имя", verifier
    )
    assert verifier.check.call_count == 2
    assert result["exists"] == "yes"
    assert result["label_strength"] == "live_card"
    assert result["verified_url_types"] == ["2gis"]
    assert result["url_outcomes"] == "verified;unreachable"


def test_contradiction_requires_long_visible_text_and_downgrades(tmp_path):
    session = Mock()
    session.get.return_value = Mock(text=page("Другой текст. " * 50))
    verifier = URLVerifier(tmp_path, session)
    result = verify_label(
        {"exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no",
         "urls": ["https://example.org/a"]},
        "Название",
        verifier,
    )
    assert result["url_outcomes"] == "contradicted"
    assert result["n_contradicted"] == 1
    assert result["exists"] == result["open"] == result["category_ok"] == "unclear"
    assert list(tmp_path.iterdir())


def test_name_variants_quoted_names_and_quote_fragments(tmp_path):
    body = "Точка Ёлка, кафе «Север» и отдельная важная фраза, Архангельск"
    verifier = verifier_for({"https://example.org/a": page(body), "https://example.org/b": page(body)}, tmp_path)
    assert verifier.verify("https://example.org/a", "Точка ЕЛКА, кофейня", "", "Архангельск") == "verified"
    assert (
        verifier.verify("https://example.org/b", "Совсем другое", "начало; отдельная важная фраза; конец",
                        "Архангельск")
        == "verified"
    )


def test_place_city_forms_address_and_silence(tmp_path):
    pages = {
        "https://e.org/inflected": page("Кафе Ромашка в Архангельске, работает каждый день"),
        "https://e.org/region": page("Кафе Ромашка, Архангельская обл., отзывы"),
        "https://e.org/address": page("Кафе Ромашка — Архангельск, улица Гагарина, 1, вход со двора"),
        "https://e.org/loose": page("Кафе Ромашка Архангельск, Гагарина, 1 телефон"),
        "https://e.org/elsewhere": page("Кафе Ромашка — Архангельск, улица Гагарина, 15, вход со двора"),
        "https://e.org/silent": page("Кафе Ромашка: меню, цены и фото блюд"),
        "https://e.org/other-city": page("Кафе Ромашка, г. Котлас, меню"),
        "https://e.org/other-region": page("Кафе Ромашка, Вологодская область, меню"),
    }
    verifier = verifier_for(pages, tmp_path)

    def outcome(key, address="Гагарина, 1"):
        return verifier.verify(f"https://e.org/{key}", "Ромашка", "", "Архангельск", address)

    assert outcome("inflected") == "verified"
    assert outcome("region") == "verified"
    assert outcome("address") == "verified"
    assert outcome("loose") == "verified"
    assert outcome("elsewhere") == "contradicted"
    assert outcome("silent") == "weak"
    assert outcome("other-city") == "contradicted"
    assert outcome("other-region") == "contradicted"
    # Novodvinsk is not the city of an Arkhangelsk object, nor the reverse.
    assert verifier.verify("https://e.org/inflected", "Ромашка", "", "Новодвинск", "Гагарина, 1") == "weak"


def test_double_address_and_house_fraction():
    assert len(object_toponyms("Мельникова, 9/Новодвинск, 50-летия Октября, 25", "Новодвинск")) == 2
    (fraction,) = object_toponyms("Гагарина, 45/3", "Архангельск")
    assert (fraction.house, fraction.suffix) == ("45", "3")
    assert object_toponyms("Сооружение", "Архангельск") == []


# The four false verifications of the 2026-11-21 diagnostic (recall_A.md §3),
# rebuilt from the titles of the cached pages.
KNOWN_FALSE = [
    (
        "https://yandex.com/maps/org/vkusno_kak_doma/26856631744/reviews/",
        "Вкусно как дома", "«Беру домой, когда нет времени готовить»", "Архангельск", "Прокопия Галушина, 7",
        "Отзывы о «Вкусно как Дома» на Тульской, Москва, Даниловская набережная, 2, корп. 4 — Яндекс Карты",
        "Беру домой, когда нет времени готовить. Вкусно как Дома — столовая.",
        "contradicted",
    ),
    (
        "https://yandex.com/maps/org/kalinka/245091702816/reviews/",
        "Калинка", "«сигарет частенько нет»", "Архангельск", "Калинина, 17к1",
        "Отзывы о «Калинка», Ленинградская область, Лодейное Поле, Ленинградское шоссе, 71Б — Яндекс Карты",
        "Магазин Калинка: сигарет частенько нет.",
        "contradicted",
    ),
    (
        "https://yandex.com/maps/org/dlya_vas/64756954724/related/",
        'Столовая "Для вас"', "", "Архангельск", "Серафимовича, 34",
        "Больше не работает: Для вас, столовая, Архангельск, Окружное шоссе, 13 — Яндекс Карты",
        "Для вас — похожие места.",
        "contradicted",
    ),
    (
        "https://www.phoenixbooks.ru/books/all",
        "Феникс", "«Феникс — улица Мельникова, 9»", "Новодвинск", "Мельникова, 9/Новодвинск, 50-летия Октября, 25",
        "Доставка книг почтой России - книжный интернет-магазин Феникс-букс Заказать книги почтой",
        "Феникс-букс: все книги издательства.",
        "weak",
    ),
]


@pytest.mark.parametrize("url,name,quote,city,address,title,body,expected", KNOWN_FALSE)
def test_known_false_verifications_synthetic(tmp_path, url, name, quote, city, address, title, body, expected):
    # Yandex ships its <head> inside a hydration script; the title must still count.
    raw = f"<html><head><script>var h = '<title>{title}</title>';</script></head><body>{FILLER}{body}</body></html>"
    verifier = verifier_for({url: raw}, tmp_path)
    assert verifier.verify(url, name, quote, city, address) == expected


@pytest.mark.parametrize("url,name,quote,city,address,title,body,expected", KNOWN_FALSE)
def test_known_false_verifications_cached(url, name, quote, city, address, title, body, expected):
    if not (URL_CACHE / (hashlib.sha256(url.encode()).hexdigest() + ".json")).exists():
        pytest.skip("audit URL cache is absent (it is not in git)")
    verifier = URLVerifier(URL_CACHE, offline=True)
    assert verifier.verify(url, name, quote, city, address) == expected


def test_closed_card_is_not_live(tmp_path):
    url = "https://yandex.ru/maps/org/romashka/1/"
    title = "Больше не работает: Ромашка, кафе, Архангельск, улица Гагарина, 1 — Яндекс Карты"
    verifier = verifier_for({url: page("Ромашка", title)}, tmp_path)
    check = verifier.check(url, "Ромашка", "", "Архангельск", "Гагарина, 1")
    assert check.outcome == "verified" and check.closed
    label = {"exists": "yes", "open": "open", "urls": [url], "evidence_date": "2026"}
    assert verify_label(label, "Ромашка", verifier, "Архангельск", "Гагарина, 1")["exists"] == "unclear"


def test_strength_dated_live_card_and_mirrors():
    fresh = Check("verified", dates=frozenset({"2025-04-12"}))
    mirror = "https://urbanplaces.su/card-1"
    dated = {"evidence_date": "2025-04-12", "audit_date": "2026-11-21T10:00:00+00:00"}
    assert label_strength(dated, "Ромашка", [(mirror, fresh)]) == "dated"
    assert label_strength({**dated, "evidence_date": "2025-04"}, "Ромашка", [(mirror, fresh)]) == "dated"
    # The date must be on a verified page, be 2025 or later, and not be the audit day.
    assert label_strength(dated, "Ромашка", [(mirror, Check("weak", dates=fresh.dates))]) == "weak"
    assert label_strength({**dated, "evidence_date": "2024-04-12"}, "Ромашка",
                          [(mirror, Check("verified", dates=frozenset({"2024-04-12"})))]) == "weak"
    today = {"evidence_date": "2026-11-21", "audit_date": "2026-11-21T10:00:00+00:00"}
    assert label_strength(today, "Ромашка", [(mirror, Check("verified", dates=frozenset({"2026-11-21"})))]) == "weak"
    assert label_strength({"evidence_date": "2026"}, "Ромашка", [(mirror, Check("verified"))]) == "weak"
    for card in ("https://yandex.ru/maps/org/romashka/1/", "https://2gis.ru/arkhangelsk/firm/7000",
                 "https://vk.com/romashka"):
        assert label_strength({}, "Ромашка", [(card, Check("verified"))]) == "live_card"
    for not_card in ("https://yandex.com/maps/user/abc", "https://2gis.ru/arkhangelsk/search/x",
                     "https://zoon.ru/arh/romashka", "https://www.spravker.ru/romashka"):
        assert label_strength({}, "Ромашка", [(not_card, Check("verified"))]) == "weak"


def test_official_site_by_domain():
    assert is_primary("https://bristol.ru/shops", "Бристоль, магазин")
    assert not is_primary("https://urbanplaces.su/card", "Бристоль")
    assert not is_primary("https://www.phoenixbooks.ru/books/all", "Гарант")


def test_evidence_day_precision():
    assert evidence_day("2025-11-26") == "2025-11-26"
    assert evidence_day("2026-01") == "2026-01"
    assert evidence_day("12.03.2025") == "2025-03-12"
    assert evidence_day("2026") == evidence_day("не указана") == evidence_day(None) == ""


def test_http_failure_is_not_cached(tmp_path):
    response = Mock(text="x" * 1000)
    response.raise_for_status.side_effect = requests.HTTPError()
    session = Mock()
    session.get.return_value = response
    verifier = URLVerifier(tmp_path, session)
    assert verifier.verify("https://example.org/a", "Name", "") == "unreachable"
    assert not list(tmp_path.iterdir())


def test_page_dates_are_read_in_three_spellings(tmp_path):
    url = "https://e.org/news"
    body = "Ромашка, Архангельск: открыто 2025-03-04, отзыв 12.05.2025 и заметка от 7 июня 2025 года"
    verifier = verifier_for({url: page(body)}, tmp_path)
    check = verifier.check(url, "Ромашка", "", "Архангельск")
    assert {"2025-03-04", "2025-05-12", "2025-06-07"} <= check.dates
    assert json.loads(next(tmp_path.iterdir()).read_text())["url"] == url
