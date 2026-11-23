"""Sport (спортивные объекты и места для физической активности): a class of the final delivery added 2026-11-24.

Nothing here is code: the rule is `categories.sport` in `config/config.yaml`
(catering's rule without ЕГАИС; see the comment above the medicine block).
"""
from __future__ import annotations

from collections.abc import Mapping

from .base import Spec

NAME = "sport"


def spec(config: Mapping) -> Spec:
    """The sport Spec from the whole workflow config."""
    return Spec.from_config(NAME, config["categories"][NAME])
