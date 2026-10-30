"""Parity of the whole catering pipeline with the archive's `confirm-final` run.

Runs every final delivery through the same steps the workflow runs -- input
adapter, all seven checks, the category rule, the XLSX writer -- in-process,
with the parquet round trips of the workflow between steps, and compares with
`resources/golden/final_confirm/`:

* `final_confirm_objects.csv` -- every column, every object of the three
  cities (both sides as CSV text, so NaN vs "" cannot hide a difference);
* each `*_подтверждено.xlsx` -- sheet `data` cell by cell, header row
  included, plus the sheet list, the auto-filter and the freeze panes;
  the legend sheet's «Итоги» exactly and its «Как читать» texts with the
  snapshot paths mapped to the new layout (the only intended change).

The registry extract is the golden one (`resources/golden/egais/`), which the
`egais_register` rule reproduces (tests/regression/test_egais_parity.py);
parsing the 10.9 GB ZIP here would cost six minutes and test nothing new.
"""
from __future__ import annotations

import io
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import yaml

from infracheck.categories import catering, pipeline, registry

ROOT = Path(__file__).resolve().parents[2]

# The archive's legend named its snapshot directories; the new layout's differ.
LEGEND_PATHS = {"data/raw_alter_overture": "resources/overture",
                "data/raw_alter_foursquare": "resources/foursquare"}


def _text(df: pd.DataFrame) -> pd.DataFrame:
    buf = io.StringIO()
    df.to_csv(buf, sep=";", index=False)
    buf.seek(0)
    return pd.read_csv(buf, sep=";", dtype=str, keep_default_na=False)


def _run(config: dict, d: pd.Series, register: Path, out: Path) -> tuple[pd.DataFrame, Path]:
    """One delivery through the workflow's steps, parquet between them as in the DAG."""
    cat = config["categories"][d["category"]]
    spec = catering.spec(config)
    pols = registry.policies(cat)
    work = out / d["delivery"]
    ob, rowmap, raw = pipeline.load(d, ROOT)
    pipeline.write_parquet(ob, work / "objects.parquet")
    ob = pipeline.read_parquet(work / "objects.parquet")
    evs = {}
    for c in spec.checks:
        res = pipeline.resource(c, config, cat, register)
        res = ROOT / res if res and not Path(res).is_absolute() else res
        pipeline.write_parquet(registry.run(c, ob, pols[c], res), work / f"{c}.parquet")
        evs[c] = pipeline.read_parquet(work / f"{c}.parquet")
    pipeline.write_parquet(pipeline.decide(ob, evs, spec), work / "decided.parquet")
    table = pipeline.read_parquet(work / "decided.parquet")
    dst = work / Path(d["path"]).name.replace(".xlsx", "_подтверждено.xlsx")
    pipeline.deliver(ROOT / d["path"], dst, raw, rowmap, table, spec, pols)
    return table.assign(file=Path(d["path"]).name), dst


@pytest.fixture(scope="module")
def built(golden, tmp_path_factory):
    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    deliveries = pd.read_csv(ROOT / "config" / "deliveries.tsv", sep="\t", dtype=str)
    deliveries = deliveries[deliveries["category"] == catering.NAME]
    out = tmp_path_factory.mktemp("catering")
    register = golden / "egais" / "egais_licences_final.tsv"
    runs = [_run(config, d, register, out) for _, d in deliveries.iterrows()]
    return pd.concat([t for t, _ in runs], ignore_index=True), [p for _, p in runs]


@pytest.mark.golden
def test_objects_parity(built, golden):
    gold = pd.read_csv(golden / "final_confirm" / "final_confirm_objects.csv", sep=";",
                       encoding="utf-8-sig", dtype=str, keep_default_na=False)
    table, _ = built
    assert len(table) == len(gold) == 1688
    missing = [c for c in gold.columns if c not in table.columns]
    assert not missing, f"columns the pipeline does not produce: {missing}"
    mine = _text(table[list(gold.columns)])
    bad = {c: int((gold[c] != mine[c]).sum()) for c in gold.columns if (gold[c] != mine[c]).any()}
    assert not bad, f"columns with mismatches: {bad}"


def _legend(ws) -> tuple[list[tuple], list[tuple]]:
    rows = list(ws.iter_rows(values_only=True))
    cut = rows.index(("Итоги", None))
    return rows[:cut], rows[cut:]


@pytest.mark.golden
def test_xlsx_parity(built, golden):
    _, paths = built
    assert len(paths) == 3
    for mine_p in paths:
        gold_p = golden / "final_confirm" / mine_p.name
        gw, mw = openpyxl.load_workbook(gold_p), openpyxl.load_workbook(mine_p)
        assert gw.sheetnames == mw.sheetnames, mine_p.name
        gs, ms = gw["data"], mw["data"]
        assert (gs.max_row, gs.max_column) == (ms.max_row, ms.max_column), mine_p.name
        assert gs.auto_filter.ref == ms.auto_filter.ref and gs.freeze_panes == ms.freeze_panes
        diffs = [(i, j, a, b)
                 for i, (gr, mr) in enumerate(zip(gs.iter_rows(values_only=True), ms.iter_rows(values_only=True),
                                                  strict=True), start=1)
                 for j, (a, b) in enumerate(zip(gr, mr, strict=True), start=1) if a != b]
        assert not diffs, f"{mine_p.name}: {len(diffs)} cells differ, first {diffs[:3]}"
        g_how, g_sum = _legend(gw["Подтверждение"])
        m_how, m_sum = _legend(mw["Подтверждение"])
        assert g_sum == m_sum, mine_p.name

        def moved(v):
            if not isinstance(v, str):
                return v
            for old, new in LEGEND_PATHS.items():
                v = v.replace(old, new)
            return v
        assert [tuple(moved(v) for v in r) for r in g_how] == m_how, mine_p.name
