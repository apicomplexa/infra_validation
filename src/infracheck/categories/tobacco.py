"""Tobacco -- not ported yet.

The registry holds no tobacco class, so a tobacco object can only be
`probable` through the alcohol licence of the counter it shares (never
summed with `direct`).  Its config block will be `categories.tobacco`; the
archive's behaviour is the tobacco layer of `arkhinfra.v4_egais` and
`resources/golden/v4_licences/v4_licences.csv`.
"""
from __future__ import annotations

NAME = "tobacco"


def spec(config):
    raise NotImplementedError(
        "tobacco is not ported yet: see the tobacco layer of arkhinfra.v4_egais "
        "(archive) and resources/golden/v4_licences/v4_licences.csv")
