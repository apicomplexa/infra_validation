"""Category pipelines: which checks, the decision rule, the texts.

`base.py` is the rule, applied to a `Spec` built from a block of
`config/config.yaml`; each category module (`catering.py`, ...) names its
block.  `registry.py` knows the check modules and builds their Policies.
"""
from __future__ import annotations

import importlib
from collections.abc import Mapping

from .base import Spec


def spec(config: Mapping, name: str) -> Spec:
    """The Spec of category `name` (raises NotImplementedError for one not ported)."""
    return importlib.import_module(f"{__name__}.{name}").spec(config)
