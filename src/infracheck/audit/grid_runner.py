"""Offline evaluation of calibration policies on frozen sample units."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path

import pandas as pd

from infracheck.categories import base, pipeline, registry

GRID_CHECKS = {"yandex", "overture", "foursquare"}
_PARQUET_CACHE: dict[tuple, pd.DataFrame] = {}
_CHECK_CACHE: dict[tuple, pd.DataFrame] = {}


def run_point(
    config: Mapping,
    category_name: str,
    category_config: Mapping,
    sample: pd.DataFrame,
    root: Path | str = ".",
) -> pd.DataFrame:
    """Evaluate one category policy over its sample_A rows.

    `category_config` is the candidate category block (including `policy`).
    Candidate policies are compared with the supplied base config so unchanged
    checks reuse their finalest Evidence snapshots.
    """
    if sample.empty:
        return pd.DataFrame(columns=["unit_id", "confirmed_object", "ver_kind"])
    candidate = _copy_mapping(category_config)
    base_category = config["categories"][category_name]
    overrides = {}
    for check in set(base_category.get("policy", {})) | set(candidate.get("policy", {})):
        before = base_category.get("policy", {}).get(check, {})
        after = candidate.get("policy", {}).get(check, {})
        changed = {key: value for key, value in after.items() if before.get(key) != value}
        if changed:
            overrides[check] = changed
    cfg = _copy_mapping(config)
    outputs = []
    for delivery, rows in sample.groupby("delivery", sort=False):
        result = evaluate_delivery(cfg, category_name, str(delivery), rows, overrides, root,
                                  category_config=candidate)
        outputs.append(result)
    out = pd.concat(outputs, ignore_index=True)
    out["confirmed_object"] = out.ver_exists.map({"conf": 1, "not": 0}).astype("int64")
    return out[["unit_id", "confirmed_object", "ver_kind"]]


def evaluate_delivery(
    config: Mapping,
    category_name: str,
    delivery: str,
    units: pd.DataFrame,
    overrides: Mapping,
    root: Path | str = ".",
    category_config: Mapping | None = None,
) -> pd.DataFrame:
    """Rejudge a delivery and pool results within the supplied frozen units.

    `units` contains sample_A rows for one delivery, including `unit_id` and
    semicolon-separated `object_ids`. Only thresholds that do not alter unit
    composition are re-run. Provider links and the shared EGAIS assignment
    stay at the finalest snapshot; neither is an allowed grid parameter.
    """
    root = Path(root)
    cfg = _copy_mapping(config)
    base_category = _copy_mapping(cfg["categories"][category_name])
    category = _copy_mapping(category_config or base_category)
    policy_cfg = category["policy"]
    current_policy = _copy_mapping(base_category["policy"])

    for check, policy_fields in overrides.items():
        if check not in GRID_CHECKS:
            raise ValueError(f"Grid override cannot change {check!r}")
        if check not in policy_cfg:
            raise KeyError(f"{category_name}: no policy for {check}")
        unknown = set(policy_fields) - {f.name for f in fields(registry.module(check).Policy)}
        if unknown:
            raise KeyError(f"{check}: unknown Policy field(s) {sorted(unknown)}")
        policy_cfg[check].update(policy_fields)

    delivery_dir = root / "data" / "temp" / delivery
    objects_path = delivery_dir / "objects.parquet"
    objects = _read_parquet(objects_path)
    if objects.index.name != "object_id":
        raise ValueError(f"{delivery}: ObjectTable index must be object_id")

    evidence_by_check = {}
    for check in category["checks"]:
        changed = current_policy.get(check, {}) != policy_cfg.get(check, {})
        if changed:
            if check not in GRID_CHECKS:
                raise ValueError(f"Grid override changed non-grid check {check!r}")
            resource = pipeline.resource(check, cfg, category, root / "data" / "temp" / "egais" / "registry.tsv")
            pol = registry.policy(check, policy_cfg.get(check))
            resource_path = root / resource if resource else None
            cache_key = (
                str(objects_path.resolve()), _fingerprint(objects_path), check,
                json.dumps(policy_cfg.get(check, {}), sort_keys=True, default=str),
                _fingerprint(resource_path) if resource_path else None,
            )
            if cache_key not in _CHECK_CACHE:
                _CHECK_CACHE[cache_key] = registry.run(
                    check, objects.copy(deep=True), pol, resource_path
                ).copy(deep=True)
            evidence_by_check[check] = _CHECK_CACHE[cache_key].copy(deep=True)
        else:
            path = delivery_dir / "evidence" / f"{check}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"Missing cached finalest Evidence: {path}")
            evidence_by_check[check] = _read_parquet(path)

    # Disable dynamic link construction inside decide: the sample's object_ids
    # are the authoritative units for every candidate policy.
    fixed_category = _copy_mapping(category)
    fixed_category["merge"] = {}
    spec = base.Spec.from_config(category_name, fixed_category)
    _ = pipeline.decide(objects, evidence_by_check, spec)

    unit_map = {}
    for row in units.itertuples(index=False):
        ids = [v for v in str(row.object_ids).split(";") if v]
        if not ids:
            raise ValueError(f"{row.unit_id}: sample unit has no object_ids")
        missing = set(ids) - set(objects.index)
        if missing:
            raise ValueError(f"{row.unit_id}: object_ids absent from {delivery}: {sorted(missing)}")
        for object_id in ids:
            if object_id in unit_map:
                raise ValueError(f"{object_id}: appears in multiple frozen sample units")
            unit_map[object_id] = row.unit_id

    selected_ids = list(unit_map)
    pooled_index = pd.Index(units.unit_id.astype(str), name="object_id")
    ev = registry.stack(evidence_by_check)
    ev = ev[ev.object_id.isin(selected_ids)].copy()
    ev["object_id"] = ev.object_id.map(unit_map)
    exists = base.existence(ev, pooled_index, spec)["confirmed"]
    kinds = base.kind(ev, pooled_index, spec)[spec.kind.column]
    out = units[["unit_id", "delivery", "category", "object_ids"]].copy()
    out["ver_exists"] = out.unit_id.map(exists).map({1: "conf", 0: "not"})
    out["ver_kind"] = out.unit_id.map(kinds)
    return out.reset_index(drop=True)


def _copy_mapping(value):
    """Recursively copy ordinary YAML mappings before applying overrides."""
    if isinstance(value, Mapping):
        return {k: _copy_mapping(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_mapping(v) for v in value]
    return value


def _fingerprint(path: Path | str) -> tuple | None:
    """Return a cheap identity for immutable inputs used by process caches."""
    path = Path(path)
    if not path.exists():
        return None
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns


def _read_parquet(path: Path | str) -> pd.DataFrame:
    path = Path(path)
    fingerprint = _fingerprint(path)
    key = fingerprint or (str(path.resolve()), None)
    if key not in _PARQUET_CACHE:
        _PARQUET_CACHE[key] = pipeline.read_parquet(path)
    return _PARQUET_CACHE[key].copy(deep=True)
