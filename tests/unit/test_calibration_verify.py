from unittest.mock import Mock

import requests

from infracheck.audit.verify import URLVerifier, source_type, verify_label


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


def test_unreachable_does_not_downgrade(tmp_path):
    session = Mock()
    session.get.side_effect = requests.Timeout()
    verifier = URLVerifier(tmp_path, session)
    label = {"exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no", "urls": ["https://2gis.ru/a"]}
    result = verify_label(label, "Название", verifier)
    assert result["exists"] == "yes"
    assert result["n_unreachable"] == 1
    assert result["url_types"] == ["2gis"]
    assert not list(tmp_path.iterdir())
    assert label["exists"] == "yes"


def test_source_domain_is_not_substring():
    assert source_type("https://maps.yandex.ru/x") == "yandex"
    assert source_type("https://yandex.ru.evil.test/x") == "other"


def test_every_citation_is_checked():
    verifier = Mock()
    verifier.verify.side_effect = ["verified", "unreachable"]
    result = verify_label({"exists": "yes", "urls": ["https://2gis.ru/a", "https://example.org/b"]}, "Имя", verifier)
    assert verifier.verify.call_count == 2
    assert result["exists"] == "yes"
    assert result["verified_url_types"] == ["2gis"]
    assert result["url_outcomes"] == "verified;unreachable"


def test_contradiction_requires_long_visible_text_and_downgrades(tmp_path):
    page = "<html><body>" + ("Другой текст. " * 50) + "</body></html>"
    session = Mock()
    session.get.return_value = Mock(text=page)
    verifier = URLVerifier(tmp_path, session)
    result = verify_label(
        {"exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no",
         "urls": ["https://example.org/a"]},
        "Название",
        verifier,
    )
    assert result["url_outcomes"] == "contradicted"
    assert result["n_contradicted"] == 1
    assert result["exists"] == result["open"] == "unclear"
    assert list(tmp_path.iterdir())


def test_name_variants_quoted_names_and_quote_fragments(tmp_path):
    page = (
        "<html><body>"
        + ("Нейтральный контекст " * 35)
        + "Точка Ёлка, кафе «Север» и отдельная важная фраза</body></html>"
    )
    session = Mock()
    session.get.return_value = Mock(text=page)
    verifier = URLVerifier(tmp_path, session)
    assert verifier.verify("https://example.org/a", "Точка ЕЛКА, кофейня", "") == "verified"
    assert (
        verifier.verify("https://example.org/b", "Совсем другое", "начало; отдельная важная фраза; конец")
        == "verified"
    )


def test_http_failure_is_not_cached(tmp_path):
    response = Mock(text="x" * 1000)
    response.raise_for_status.side_effect = requests.HTTPError()
    session = Mock()
    session.get.return_value = response
    verifier = URLVerifier(tmp_path, session)
    assert verifier.verify("https://example.org/a", "Name", "") == "unreachable"
    assert not list(tmp_path.iterdir())
