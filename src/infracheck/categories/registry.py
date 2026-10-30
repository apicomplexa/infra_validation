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
    cls = module(check).Policy
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(cfg) - names
    if unknown:
        raise KeyError(f"{check}: Policy has no field(s) {sorted(unknown)}")
    return cls(**cfg)


def policies(category: Mapping) -> dict[str, object]:
    """Every check's Policy for one category block of config.yaml."""
    return {c: policy(c, category.get("policy", {}).get(c)) for c in category["checks"]}


def run(check: str, objects: pd.DataFrame, pol, resource: Path | str | None = None) -> pd.DataFrame:
    """Evidence of one check for one ObjectTable.

    For `egais`, `resource` is the parsed extract and `objects` may be several
    deliveries stacked (a match group, see `pipeline.egais_group`); the
    register is cut to the objects' own settlements first.
    """
    mod = module(check)
    if check == "yandex":
        cache = own_queries(objects, mod.load_cache(resource) if resource else {})
        ev = mod.run(objects, pol, cache=cache)
    elif check in ("overture", "foursquare"):
        ev = mod.run(objects, mod.load(resource), pol)
    elif check == "egais":
        register = load_register(resource, settlements(objects))
        if register is None:
            raise FileNotFoundError(f"egais: no registry extract at {resource}")
        ev = mod.run(objects, register, pol)
    else:
        ev = mod.run(objects, pol)
    evidence.validate(ev)
    return ev


def own_queries(objects: pd.DataFrame, cache: dict[str, dict]) -> dict[str, dict]:
    """The cached answers to *each object's own* query.

    The cache is keyed by `site_key`, and a v4 site key is the coordinate
    alone: an alcohol and a tobacco record at one door share it, though only
    one of them was queried, by its own name.  An answer counts for an object
    only if its search URL is the one this object's query would read.
    """
    from infracheck.checks import yandex as y
    out = {}
    for _, r in objects.iterrows():
        rec = cache.get(r["site_key"])
        if rec is None:
            continue
        url = y.search_url(r["name"], r["address"], float(r["lat"]), float(r["lon"]), r["city"],
                           r.get("yandex_region") or y.DEFAULT_REGION)
        if rec.get("search_url") == url:
            out[r["site_key"]] = rec
    return out


def settlements(objects: pd.DataFrame) -> set[str]:
    """The registry settlement names of the objects' cities ("архангельск")."""
    return {str(c).strip().lower() for c in objects["city"].unique() if str(c).strip()}


def load_register(path: Path | str | None, keep: set[str] | None = None) -> pd.DataFrame | None:
    """The parsed extract as a licmatch RegisterTable, cut to `keep` settlements (None when absent).

    The cut is what the archive did by reading a one-city extract: the v4
    join learns brands from base rates over the whole register it is given,
    so rows of other cities are not neutral there.
    """
    if not path or not Path(path).exists():
        return None
    from infracheck.registers import egais as reg
    eg = reg.load(path)
    if keep:
        eg = eg[eg["outlet_settlement"].map(reg.settlement_name).isin(keep)].reset_index(drop=True)
    return reg.to_register(eg)


def wide(check: str, ev: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """The check's archive-shaped columns for every object of `index`."""
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
