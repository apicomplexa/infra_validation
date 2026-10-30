"""Alcohol: off-trade shops and on-trade bars / catering.

Nothing here is code: the rule is `categories.alcohol` in `config/config.yaml`,
applied by `base.py`.  Until the final alcohol XLSX arrives it runs on the
fourth delivery's alcohol CSV (`inputs.v4_csv`); the final file will be one
more row of `deliveries.tsv`.

What is alcohol-specific and why (the config's comments carry the details):

* **The licence registry is a source here -- under the catering standard.**
  The v4 join (`checks.egais`, mode `v4`) assigns each record the licence it
  holds, one outlet per shop across the alcohol *and* tobacco layers (the two
  share `match_group` v4).  That join admits a licence on the address alone,
  so the licence counts for «Подтверждено» only when the name agrees as well
  -- a known chain, a learned fascia -> INN pair that survives leave-one-out,
  or the names themselves (`checks.egais.name_agrees`).  An own licence won
  on the address alone, or by a generic fascia («Продукты»), is weak and is
  shown after « || »; an archived own licence is inactive.
* **Off-trade and on-trade are kept apart** -- the registry licenses them
  separately, and only off-trade compares with a count of shops -- in the
  summary lines and in the appended `trade` column.
* **«Алкоголь?»** reads the object's own descriptions and its own in-force
  licence (strong only).  A grocery or a café says neither yes nor no.
"""
from __future__ import annotations

from collections.abc import Mapping

from .base import Spec

NAME = "alcohol"


def spec(config: Mapping) -> Spec:
    """The alcohol Spec from the whole workflow config."""
    return Spec.from_config(NAME, config["categories"][NAME])
