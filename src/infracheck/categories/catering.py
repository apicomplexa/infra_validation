"""Catering (общепит): the final delivery's first object class.

Nothing here is code: the whole rule is `categories.catering` in
`config/config.yaml` -- seven sources (OSM, 2GIS, Yandex, Overture,
Foursquare, the city register, an own ЕГАИС catering licence), two of them
enough, and «Общепит?» read from the export's own descriptor, Yandex's rubric
and the FSQ / Overture categories.  The vocabularies (Russian ladder, FSQ
taxonomy, Overture categories) are config too, so a reviewer can read and
change them without reading Python.  `base.py` applies it.

What is catering-specific and why, in one place (the comments of the config
block carry the details):

* The contractor's raw Yandex «Найдено» is weak: its rule is address +
  category, so it says "a catering business is at this door", which the miss
  text names (`policy.yandex.contractor_miss_text`).
* The registry counts only an in-force *catering* licence with the name
  agreeing -- an address alone never confirms (malls, hotels, food courts).
* Two parity deviations from the three-axes default, kept on purpose and
  marked PARITY in the config: weak FSQ / Overture records vote on the kind,
  and the «Общепит?» column is written empty, as the archive delivered it.
"""
from __future__ import annotations

from collections.abc import Mapping

from .base import Spec

NAME = "catering"


def spec(config: Mapping) -> Spec:
    """The catering Spec from the whole workflow config."""
    return Spec.from_config(NAME, config["categories"][NAME])
