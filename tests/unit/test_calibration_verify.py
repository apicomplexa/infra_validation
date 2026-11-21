from unittest.mock import Mock

import requests

from infracheck.audit.verify import URLVerifier, source_type, verify_label


def test_fetch_cache_and_page_text(tmp_path):
    response = Mock(text="<html><body>Кафе Ромашка работает</body></html>")
    session = Mock()
    session.get.return_value = response
    verifier = URLVerifier(tmp_path, session)
    assert verifier.verify("https://example.org/", "Ромашка", "")
    assert verifier.verify("https://example.org/", "", "Кафе Ромашка")
    assert not verifier.verify("https://example.org/", "", "body")
    session.get.assert_called_once()
    assert session.get.call_args.kwargs["timeout"] == 15


def test_failures_downgrade_but_preserve_original(tmp_path):
    session = Mock()
    session.get.side_effect = requests.Timeout()
    verifier = URLVerifier(tmp_path, session)
    label = {"exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no", "urls": ["https://2gis.ru/a"]}
    result = verify_label(label, "Название", verifier)
    assert result["exists"] == result["open"] == "unclear"
    assert '"exists": "yes"' in result["notes"]
    assert result["url_types"] == ["2gis"]
    assert not list(tmp_path.iterdir())
    assert label["exists"] == "yes"


def test_source_domain_is_not_substring():
    assert source_type("https://maps.yandex.ru/x") == "yandex"
    assert source_type("https://yandex.ru.evil.test/x") == "other"


def test_every_citation_is_checked():
    verifier = Mock()
    verifier.verify.side_effect = [True, False]
    result = verify_label({"exists": "yes", "urls": ["https://2gis.ru/a", "https://example.org/b"]}, "Имя", verifier)
    assert verifier.verify.call_count == 2
    assert result["exists"] == "yes"
    assert result["verified_url_types"] == ["2gis"]
