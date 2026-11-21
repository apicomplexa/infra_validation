"""Deterministic stratified sampling of merged delivery objects."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

BLIND = ["unit_id", "name", "address", "city", "claimed_category", "lat", "lon"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def boolean(value):
    value = str(value).strip().lower()
    if value in {"true", "1", "yes", "да"}:
        return True
    if value in {"false", "0", "no", "нет"}:
        return False
    raise ValueError(f"Invalid boolean: {value!r}")


def allocate(capacity, total, weights, minimum=None):
    """Allocate integer slots with minimums, caps and deterministic remainders."""
    keys = sorted(capacity)
    result = {k: min(capacity[k], (minimum or {}).get(k, 0)) for k in keys}
    if sum(result.values()) > total or sum(capacity.values()) < total:
        raise ValueError("Requested sample size cannot satisfy stratum constraints")
    while sum(result.values()) < total:
        available = [k for k in keys if result[k] < capacity[k]]
        remaining = total - sum(result.values())
        denom = sum(weights[k] for k in available)
        quotas = {k: remaining * weights[k] / denom for k in available}
        additions = {k: min(capacity[k] - result[k], math.floor(quotas[k])) for k in available}
        if sum(additions.values()) == 0:
            k = min(available, key=lambda x: (-quotas[x], x))
            additions[k] = 1
        for k, value in additions.items():
            result[k] += value
    return result


def build_frame(records, seed, supervisor=()):
    required = {"delivery", "merged", "object_id", "confirmed_object", "confirmed", "needs_yandex", "ya_status"}
    missing = required - set(records.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if records[list(required - {"ya_status"})].isna().any().any():
        raise ValueError("Missing required values")
    if (records.needs_yandex.map(boolean) & records.ya_status.fillna("").astype(str).str.strip().eq("")).any():
        raise ValueError("Yandex checks are incomplete")
    rows = []
    for (delivery, merged), group in records.groupby(["delivery", "merged"], sort=True):
        group = group.sort_values("object_id", key=lambda s: s.astype(str).str.rsplit(":", n=1).str[-1].astype(int))
        rep = group.iloc[0]
        pooled = group.confirmed_object.map(boolean)
        if pooled.nunique() != 1:
            raise ValueError(f"Conflicting pooled verdicts: {delivery}:{merged}")
        conf = pooled.iloc[0]
        kind_col = {"catering": "food", "alcohol": "alcohol", "tobacco": "tobacco"}[rep.category]
        kinds = group[kind_col].fillna("нет данных").unique()
        kind = kinds[0] if len(kinds) == 1 else "mixed"
        stratum = (
            "S4"
            if not conf
            else ("S1" if kind == "да" else "S2" if kind in {"нет", "автомат", "противоречие"} else "S3")
        )
        unit = f"{delivery}:{merged}"
        row = dict(
            unit_id=unit,
            delivery=delivery,
            category=rep.category,
            city=rep.city,
            merged=merged,
            object_ids=";".join(group.object_id.astype(str)),
            n_records=len(group),
            stratum=stratum,
            ver_exists="conf" if conf else "not",
            mixed=bool((group.confirmed.map(boolean) != conf).any()),
            ver_kind=kind,
            n_sources_max=group.n_sources.max(),
            sources=";".join(
                sorted({s.strip() for v in group.sources.fillna("") for s in str(v).split(";") if s.strip()})
            ),
            ya_status=";".join(sorted(set(group.ya_status.fillna("")) - {""})),
            rank=digest(f"{seed}|{delivery}|{merged}".encode()),
            supervisor_checked=unit in supervisor,
            name=rep["name"],
            address=rep.address,
            lat=rep.lat,
            lon=rep.lon,
            claimed_category=f"{rep.category}: {rep.types}",
        )
        for col in (c for c in group.columns if c.startswith("src_")):
            row[col] = any(boolean(v) for v in group[col].dropna())
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values(["delivery", "stratum", "rank", "unit_id"]).reset_index(drop=True)
    frame["rank_in_stratum"] = frame.groupby(["delivery", "stratum"]).cumcount() + 1
    return frame


def sample(config_path="config/calibration.yaml"):
    config_path = Path(config_path)
    cfg = yaml.safe_load(config_path.read_text())
    paths = cfg["paths"]
    out = Path(paths["output"])
    if (out / "manifest.json").exists():
        raise FileExistsError("Manifest exists; resampling requires explicit removal and change record")
    if cfg.get("exclude_ids"):
        raise ValueError("Exclusions alter the population; empty exclude_ids required")
    inputs = [Path(paths["objects_dir"]) / f"{c}_objects.csv" for c in ("catering", "alcohol", "tobacco")]
    inputs += [Path(paths["deliveries"]), Path(paths["pipeline_config"]), config_path]
    supervisor = set()
    if paths.get("supervisor_checked"):
        p = Path(paths["supervisor_checked"])
        inputs.append(p)
        supervisor = set(pd.read_csv(p)["unit_id"])
    data = pd.concat([pd.read_csv(p, sep=";", encoding="utf-8-sig") for p in inputs[:3]], ignore_index=True)
    frame = build_frame(data, cfg["seed"], supervisor)
    deliveries = pd.read_csv(paths["deliveries"], sep="\t")
    if set(frame.delivery) != set(deliveries.delivery):
        raise ValueError("Object deliveries do not match deliveries file")
    frame["sample"] = "reserve"
    allocations = []
    for delivery, group in frame.groupby("delivery", sort=True):
        if group.city.nunique() != 1:
            raise ValueError(f"Multiple cities in {delivery}")
        city = group.city.iloc[0]
        capacities = group.groupby("stratum").size().to_dict()
        b = allocate(
            capacities,
            cfg["n_B"][city],
            {k: math.sqrt(v) for k, v in capacities.items()},
            {k: min(v, cfg["min_per_stratum"]) for k, v in capacities.items()},
        )
        remaining = {k: v - b[k] for k, v in capacities.items()}
        a = allocate(remaining, cfg["n_A"][city], {k: 1 for k in capacities})
        for s, n in capacities.items():
            mask = (frame.delivery == delivery) & (frame.stratum == s)
            rank = frame.rank_in_stratum
            frame.loc[mask & (rank <= b[s]), "sample"] = "B"
            frame.loc[mask & (rank > b[s]) & (rank <= b[s] + a[s]), "sample"] = "A"
            allocations.append(
                dict(
                    delivery=delivery,
                    stratum=s,
                    N_h=n,
                    n_B_h=b[s],
                    n_A_h=a[s],
                    w_B=n / b[s] if b[s] else None,
                    w_A=n / a[s] if a[s] else None,
                    f_B=b[s] / n,
                    census=b[s] == n,
                )
            )
    alloc = pd.DataFrame(allocations)
    out.mkdir(parents=True, exist_ok=True)
    frame.drop(columns=["name", "address", "claimed_category", "lat", "lon"]).to_csv(out / "frame.csv", index=False)
    alloc.to_csv(out / "allocation.tsv", sep="\t", index=False)
    for name in ("A", "B"):
        sub = frame[frame["sample"] == name].copy()
        sub = sub.merge(alloc[["delivery", "stratum", f"w_{name}"]], on=["delivery", "stratum"], validate="many_to_one")
        sub.drop(columns=["name", "address", "claimed_category", "lat", "lon"]).to_csv(
            out / f"sample_{name}.csv", index=False
        )
        sub[BLIND].sort_values("unit_id").to_csv(out / f"auditor_input_{name}.csv", index=False)
    categories = yaml.safe_load(Path(paths["pipeline_config"]).read_text())["categories"]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    manifest = dict(
        seed=cfg["seed"],
        inputs={str(p): digest(p.read_bytes()) for p in inputs},
        commit=commit,
        categories_sha256=digest(json.dumps(categories, ensure_ascii=False, sort_keys=True).encode()),
        date=date.today().isoformat(),
        sampler_version=1,
    )
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
