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
