"""The finalest overlay switches four Yandex rules on for catering, alcohol and tobacco.

They are set in `config/finalest.yaml`, not in `config/config.yaml`, so the
archive deliveries the golden parity tests run (final, v4) keep the old rule;
these tests guard both halves of that split
(docs/changes/2026-11-24-finalest-yandex-rules.md).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from snakemake.utils import update_config

from infracheck.categories import base, registry
from infracheck.checks import yandex

ROOT = Path(__file__).resolve().parents[2]
OURS = ["catering", "alcohol", "tobacco"]


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plain() -> dict:
    return _load("config.yaml")


@pytest.fixture(scope="module")
def overlaid() -> dict:
    cfg = _load("config.yaml")
    update_config(cfg, _load("finalest.yaml"))  # as `snakemake --configfile` merges it
    return cfg


@pytest.mark.parametrize("cat", OURS)
def test_overlay_switches_the_rules_on(overlaid, cat):
    block = overlaid["categories"][cat]
    pol = registry.policy("yandex", block["policy"]["yandex"])
    assert pol.closed_overrides_contractor and pol.fallback_vague_address
    assert pol.exclusive_m == 50.0
    assert pol.prefer_open_m == 0.0  # a switch, left off
    assert base.Spec.from_config(cat, block).query_all


@pytest.mark.parametrize("cat", OURS)
def test_overlay_keeps_the_category_texts(plain, overlaid, cat):
    """The merge adds keys; the category's own matching and texts stay its own."""
    before = plain["categories"][cat]["policy"]["yandex"]
    after = overlaid["categories"][cat]["policy"]["yandex"]
    assert {k: after[k] for k in before} == before
    if cat != "catering":  # the overlay's older risk-layer key survives beside the new ones
        assert overlaid["categories"][cat]["policy"]["egais"]["home_cities"]


@pytest.mark.parametrize("cat", OURS)
def test_archive_config_keeps_the_old_rule(plain, cat):
    """config.yaml alone (the golden parity runs) leaves every new rule at its default."""
    block = plain["categories"][cat]
    pol = registry.policy("yandex", block["policy"]["yandex"])
    for f in ("closed_overrides_contractor", "fallback_vague_address", "exclusive_m", "prefer_open_m"):
        assert getattr(pol, f) == getattr(yandex.DEFAULT, f)
    assert not base.Spec.from_config(cat, block).query_all
