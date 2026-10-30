"""Which check modules exist, how each one's Policy is built, and how it is run.

The checks (`infracheck.checks.*`) have different inputs -- providers reads only
the ObjectTable, Yandex a cache, Overture and Foursquare a snapshot, ЕГАИС a
register -- and different `run()` signatures.  This is the one place that
knows them, so the workflow scripts and the regression test drive every check
the same way, from a category's config block:

    policies = registry.policies(config["categories"]["catering"])
    ev = registry.run("overture", objects, policies["overture"], resource=path)
    wide = registry.wide("overture", ev, objects.index)

A Policy is the check's own frozen dataclass, built with the keys of
`categories.<name>.policy.<check>`; a key the dataclass does not have is an
error, not a silent default.
"""
from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from infracheck.core import evidence

# check name -> module, imported on first use.
MODULES = {
    "providers": "infracheck.checks.providers",
    "arkh_admin": "infracheck.checks.arkh_admin",
    "export_name": "infracheck.checks.export_name",
    "yandex": "infracheck.checks.yandex",
    "overture": "infracheck.checks.overture",
    "foursquare": "infracheck.checks.foursquare",
    "egais": "infracheck.checks.egais",
}

# What each check reads besides the ObjectTable: a key of `config.resources`
# (or "register" for the parsed ЕГАИС extract, a workflow intermediate).
RESOURCE = {"yandex": "yandex_cache", "overture": "overture", "foursquare": "foursquare",
            "egais": "register"}


def module(check: str):
    return importlib.import_module(MODULES[check])


def policy(check: str, cfg: Mapping | None):
    """The check's Policy dataclass from its config block (unknown keys raise)."""
    cfg = dict(cfg or {})
    if check == "egais":
        return egais_policy(cfg)
    cls = module(check).Policy
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(cfg) - names
    if unknown:
        raise KeyError(f"{check}: Policy has no field(s) {sorted(unknown)}")
    return cls(**cfg)


# `licmatch.Policy` fields a config block may set besides `params`: the scalar
# switches of the rule.  The structural parts (kinds, the explanation ladder,
# exclusivity) are the check's own code -- `checks.egais.catering_policy`.
EGAIS_FIELDS = {"require_in_force", "name_policy", "name_fields", "unit", "home_cities"}


def egais_policy(cfg: Mapping):
    """The registry join's `licmatch.Policy`: the check's mode, thresholds from config.

    `mode` picks the check's own Policy (`catering` -> `catering_policy()`);
    `params` overrides fields of its `licmatch.MatchParams` (weights, both
    floors, the radius); the keys of `EGAIS_FIELDS` override the rule's
    switches.  Unknown keys raise.
    """
    mod = module("egais")
    cfg = dict(cfg)
    mode = cfg.pop("mode", "catering")
    if mode != "catering":
        raise NotImplementedError(f"egais mode {mode!r}: only catering is wired into the categories")
    base = mod.catering_policy()
    params = cfg.pop("params", {}) or {}
    pnames = {f.name for f in dataclasses.fields(base.params)}
    unknown = (set(params) - pnames) | (set(cfg) - EGAIS_FIELDS)
    if unknown:
        raise KeyError(f"egais: unknown policy key(s) {sorted(unknown)}")
    over = {k: (tuple(v) if isinstance(v, list) else v) for k, v in cfg.items()}
    return dataclasses.replace(base, params=dataclasses.replace(base.params, **params), **over)


def policies(category: Mapping) -> dict[str, object]:
    """Every check's Policy for one category block of config.yaml."""
    return {c: policy(c, category.get("policy", {}).get(c)) for c in category["checks"]}


def run(check: str, objects: pd.DataFrame, pol, resource: Path | str | None = None) -> pd.DataFrame:
    """Evidence of one check for one delivery's ObjectTable."""
    mod = module(check)
    if check == "yandex":
        cache = mod.load_cache(resource) if resource else {}
        ev = mod.run(objects, pol, cache=cache)
    elif check in ("overture", "foursquare"):
        ev = mod.run(objects, mod.load(resource), pol)
    elif check == "egais":
        register = load_register(resource)
        if register is None:
            raise FileNotFoundError(f"egais: no registry extract at {resource}")
        ev = mod.run_catering(objects, register, pol)
    else:
        ev = mod.run(objects, pol)
    evidence.validate(ev)
    return ev


def load_register(path: Path | str | None) -> pd.DataFrame | None:
    """The parsed registry extract as a licmatch RegisterTable (None when absent)."""
    if not path or not Path(path).exists():
        return None
    from infracheck.registers import egais as reg
    return reg.to_register(reg.load(path))


def wide(check: str, ev: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """The check's archive-shaped columns for every object of `index`."""
    if check == "egais":   # one row per object it was run on; no index argument
        w = module(check).wide(ev).reindex(index)
        w["egais_match"] = w["egais_match"].fillna(False).astype(bool)
        return w
    return module(check).wide(ev, index)


def wide_all(evs: Mapping[str, pd.DataFrame], index: pd.Index) -> pd.DataFrame:
    """Every check's wide columns side by side, in `evs` order."""
    return pd.concat([wide(c, ev, index) for c, ev in evs.items()], axis=1)


def stack(evs: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Every check's Evidence as one frame, canonical columns only, in `evs` order.

    Check-specific columns are kept only where the category rule reads them
    by name (`how` parts key on e.g. `yandex_status`), so they are carried
    along; a column absent from a check is "" for its rows.
    """
    frames = [ev for ev in evs.values() if len(ev)]
    if not frames:
        return evidence.empty()
    out = pd.concat(frames, ignore_index=True)
    for c in out.columns:
        if c not in evidence.COLUMNS and out[c].dtype == object:
            out[c] = out[c].fillna("")
    return out
