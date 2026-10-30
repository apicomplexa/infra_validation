"""Tobacco: shops, kiosks, vape shops and hookah lounges.

Nothing here is code: the rule is `categories.tobacco` in `config/config.yaml`,
applied by `base.py`.  Until the final tobacco XLSX arrives it runs on the
fourth delivery's tobacco CSV (`inputs.v4_csv`).

What is tobacco-specific and why:

* **The licence registry is no source.**  ФСРАР licenses alcohol; it has no
  tobacco class.  The registry check still runs -- the v4 join needs both
  layers together to give each outlet to one shop -- but `egais` is in no
  independence group and casts no kind vote.  What it found is shown after
  « || » only, worded as what it is: an own in-force retail *alcohol* licence
  is an indirect hint (`probable`: alcohol and tobacco share a counter), never
  a verification of the tobacco tag, and is never summed with a direct one.
* **The layer has no independent benchmark** (CLAUDE.md of the archive):
  «Подтверждено» says the object exists, not that it sells tobacco, and the
  legend says so.
* **«Табак?»** reads the object's own descriptions only; a grocery or a kiosk
  says neither yes nor no.
"""
from __future__ import annotations

from collections.abc import Mapping

from .base import Spec

NAME = "tobacco"


def spec(config: Mapping) -> Spec:
    """The tobacco Spec from the whole workflow config."""
    return Spec.from_config(NAME, config["categories"][NAME])
