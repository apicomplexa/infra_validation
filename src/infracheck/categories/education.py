"""Education (школы, детские сады, вузы, колледжи, допобразование): a class of the final delivery added 2026-11-24.

Nothing here is code: the rule is `categories.education` in `config/config.yaml`
(catering's rule without ЕГАИС; see the comment above the medicine block).
"""
from __future__ import annotations

from collections.abc import Mapping

from .base import Spec

NAME = "education"


def spec(config: Mapping) -> Spec:
    """The education Spec from the whole workflow config."""
    return Spec.from_config(NAME, config["categories"][NAME])
