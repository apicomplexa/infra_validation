"""One delivery through the whole chain, step by step.

The Snakemake rules call these one per rule (objects -> evidence per check ->
decide -> deliver); the regression test calls them in a row, in-process.
Either way the same functions run, so a parity result speaks for the
workflow.  The archive's run order was `scripts/confirm_final.py`.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from infracheck.deliver import xlsx
from infracheck.inputs import final_xlsx, v4_csv

from . import base, registry
from .base import Spec

# format of deliveries.tsv -> (path, delivery, yandex_region, category) -> (ObjectTable, RowMap, raw rows)
INPUTS = {"final_xlsx": final_xlsx.load, "v4_csv": v4_csv.load_delivery}


def load(delivery: Mapping, root: Path | str = ".") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(ObjectTable, RowMap, raw rows) of one row of `deliveries.tsv`."""
    load_fn = INPUTS.get(delivery["format"])
    if load_fn is None:
        raise NotImplementedError(f"no input adapter for format {delivery['format']!r}")
    return load_fn(Path(root) / delivery["path"], delivery["delivery"], delivery["yandex_region"],
                   delivery["category"])


def resource(check: str, config: Mapping, category: Mapping, register: Path | str | None = None):
    """The file a check reads besides the objects, from `config.resources` (None if none)."""
    key = registry.RESOURCE.get(check)
    if key is None:
        return None
    if key == "register":
        return register
    if key == "yandex_cache":
        return config["resources"]["yandex_cache"][category["yandex_cache"]]
    return config["resources"][key]


def _is_number(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))


def parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """`df` with every object column given one type, so pyarrow can write it.

    The archive's wide columns mix "" with floats (`ya_distance_m`) and NaN with
    strings (a check's extra column on another check's rows).  A column whose
    non-empty values are all numbers becomes float with "" -> NaN; any other
    becomes text with NaN -> "".  Both directions are invisible downstream: a
    CSV writes NaN and "" alike, and openpyxl leaves either cell empty.
    """
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if s.dtype != object:
            continue
        vals = [v for v in s if not (v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v))
                                     or (isinstance(v, str) and v == ""))]
        if vals and all(_is_number(v) for v in vals):
            out[c] = pd.to_numeric(s.replace("", np.nan)).astype("float64")
        elif vals and all(isinstance(v, (bool, np.bool_)) for v in vals):
            out[c] = s.fillna(False).astype(bool)
        else:
            out[c] = s.where(s.notna(), "").astype(str)
    return out


def write_parquet(df: pd.DataFrame, path: Path | str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    parquet_safe(df).to_parquet(path)


def read_parquet(path: Path | str) -> pd.DataFrame:
    return pd.read_parquet(path)


def decide(objects: pd.DataFrame, evs: Mapping[str, pd.DataFrame], spec: Spec) -> pd.DataFrame:
    """The decided object table (see `base.decide`), checks in `spec.checks` order."""
    evs = {c: evs[c] for c in spec.checks}
    return base.decide(objects, registry.stack(evs), registry.wide_all(evs, objects.index), spec)


def egais_group(objects: list[pd.DataFrame], policy, register: Path | str) -> pd.DataFrame:
    """The registry check over every delivery of one match group, stacked in order.

    The v4 join gives one outlet to one shop across *both* risk layers, so
    their deliveries are matched together and split afterwards
    (`split_group`).  Order is the archive's -- tobacco first -- and it
    matters: ties break on the lower row.
    """
    return registry.run("egais", pd.concat(objects), policy, register)


def split_group(ev: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """The rows of a group's Evidence that belong to one delivery's objects."""
    return ev[ev["object_id"].isin(index)].reset_index(drop=True)


def summary_block(title: str, table: pd.DataFrame, rowmap: pd.DataFrame, spec: Spec) -> str:
    """One delivery's block of the combined summary, from the decided table and its RowMap.

    The row-level lines need only each row's `confirmed`, which the RowMap
    carries over exactly as `deliver` broadcasts it.
    """
    rows = rowmap.join(table[["confirmed"]], on="object_id")
    return base.summary_text(title, base.summarise(table, rows, spec), table, spec)


def deliver(src: Path | str, dst: Path | str, raw: pd.DataFrame, rowmap: pd.DataFrame,
            table: pd.DataFrame, spec: Spec, policies: Mapping[str, object]) -> list[tuple[str, object]]:
    """Write the confirmed copy of `src` to `dst`; return its summary lines."""
    layout = spec.raw["deliver"]
    cols = [f["column"] for f in layout["front"]] + [a[1] for a in layout["appended"]]
    rows = base.to_rows(raw, rowmap, table, cols)
    summary = base.summarise(table, rows, spec)
    legend = xlsx.legend(layout, policies, spec.min_sources, spec.min_sources_genitive)
    xlsx.write(src, dst, rows, xlsx.sheet_columns(raw), summary, legend, layout)
    return summary
