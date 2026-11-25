"""Shared fixtures: repository paths and the golden-output guard."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "resources" / "golden"


def pytest_configure(config):
    config.addinivalue_line("markers", "golden: regression against resources/golden (skipped when absent)")


@pytest.fixture(scope="session")
def golden() -> Path:
    if not GOLDEN.exists():
        pytest.skip("resources/golden is absent (it is not in git)")
    return GOLDEN


# The archive's Yandex answers for the `final` deliveries are dated up to its tag
# (archive-2026-10-30; checks of 2026-10-29). The same cache file is shared with
# finalest catering, whose fetches (2026-11-24 on) append answers to queries the
# archive never asked or asked unanswered; read whole, they move the golden run.
# The regression replays the cache as the archive saw it -- the input is pinned,
# the comparison stays exact.
ARCHIVE_YANDEX_UNTIL = "2026-10-31"
FINAL_YANDEX_CACHE = ROOT / "resources" / "cache" / "yandex" / "final_yandex_cache.jsonl"


@pytest.fixture(scope="session")
def archive_final_cache(tmp_path_factory) -> Path:
    """`final_yandex_cache.jsonl` cut to the lines checked on or before the archive tag."""
    import json

    out = tmp_path_factory.mktemp("yandex") / "final_yandex_cache.jsonl"
    lines = [ln for ln in FINAL_YANDEX_CACHE.read_text(encoding="utf-8").splitlines()
             if ln.strip() and json.loads(ln)["checked"] <= ARCHIVE_YANDEX_UNTIL]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
