"""Alcohol retail and on-trade -- not ported yet.

The rule comes next: the v4 join (`licmatch` in `learned` brand mode, outlet
exclusive across the alcohol and tobacco layers, the archived-licence rule)
plus the directory checks.  Its config block will be `categories.alcohol`;
the archive's behaviour is `arkhinfra.v4_egais` / `v4_yandex` and the golden
outputs are `resources/golden/v4_licences/` and `resources/golden/v4_yandex/`.
"""
from __future__ import annotations

NAME = "alcohol"


def spec(config):
    raise NotImplementedError(
        "alcohol is not ported yet: see arkhinfra.v4_egais (archive) and "
        "resources/golden/v4_licences/ for the behaviour to reproduce")
