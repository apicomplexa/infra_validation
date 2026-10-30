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
  snapshot paths mapped to the new layout.

Exact, except for the two archive bugs fixed on 2026-10-30
(docs/changes/2026-10-30-overture-fsq-and-food-column.md), whose effect is
asserted here object by object rather than tolerated:

* an Overture place built from Foursquare alone no longer counts: exactly the
  21 objects of `OVERTURE_FSQ_ONLY` lose that source -- `src_overture`,
  `n_sources` (one less), `sources` (without «Overture») and `reasons`
  (without the Overture clause) change, nothing else, and no «Подтверждено»;
* «Общепит?» is filled: every data row carries its object's `food`, where
  the archive left the column empty.

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

# Fix 1: objects whose only counted Overture place is "Foursquare|Overture" (site_key).
OVERTURE_FSQ_ONLY = frozenset({
    'Архангельск|Coffee Like|64.536199|40.51941',
    'Архангельск|Craft Beery, пивной бар-ресторан|64.535983|40.53165',
    'Архангельск|Вкусно — и точка|64.532004|40.529429',
    'Архангельск|Боброфф|64.547317|40.514118',
    'Архангельск|Важный Анчоус, суши-рамен-бар|64.562585|40.531799',
    'Архангельск|Вкусно — и точка. Авто, автокафе|64.541844|40.529157',
    'Архангельск|Стройбат, склад-магазин|64.554665|40.580724',
    'Архангельск|Вкусно — и точка|64.541782|40.529053',
    'Архангельск|Вкусно — и точка, предприятие быстрого питания|64.532122|40.529354',
    'Архангельск|Вкусно — и точка, предприятие быстрого питания|64.542182|40.535021',
    'Архангельск|Пиццерия "Престо"|64.546834|40.575057',
    'Архангельск|Кухня, ресторан|64.555727|40.531412',
    'Архангельск|Кухня|64.555676|40.531485',
    'Северодвинск|#хочуЛето|64.566499|39.84007',
    'Северодвинск|Бонсай|64.557833|39.859197',
    'Северодвинск|Вкусно — и точка, предприятие быстрого питания|64.536252|39.77859',
    'Северодвинск|#ХОЧУЛЕТО|64.566424|39.840005',
    'Северодвинск|Чайхона, служба доставки блюд|64.569123|39.837635',
    'Северодвинск|Фреш|64.593467|39.806829',
    'Северодвинск|Вкусно — и точка|64.536249|39.778425',
    'Северодвинск|Фреш|64.593511|39.806825',
})
FIX_OVERTURE = ["src_overture", "n_sources", "sources", "reasons"]
# The same objects' cells on the data sheet.
FIX_OVERTURE_CELLS = ["Источников", "Источники", "Основания"]
# Fix 2: the column the archive wrote the header of only.
FOOD_CELL = "Общепит?"


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
    deliveries = pd.read_csv(ROOT / "config" / "deliveries.tsv", sep="	", dtype=str)
    deliveries = deliveries[deliveries["category"] == catering.NAME]
    out = tmp_path_factory.mktemp("catering")
    register = golden / "egais" / "egais_licences_final.tsv"
    runs = [_run(config, d, register, out) for _, d in deliveries.iterrows()]
    return pd.concat([t for t, _ in runs], ignore_index=True), [p for _, p in runs]


@pytest.fixture(scope="module")
def gold(golden) -> pd.DataFrame:
    return pd.read_csv(golden / "final_confirm" / "final_confirm_objects.csv", sep=";",
                       encoding="utf-8-sig", dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def mine(built, gold) -> pd.DataFrame:
    table, _ = built
    assert len(table) == len(gold) == 1688
    missing = [c for c in gold.columns if c not in table.columns]
    assert not missing, f"columns the pipeline does not produce: {missing}"
    return _text(table[list(gold.columns)])


def _without_overture(reasons: str) -> str:
    """The archive's «Основания» with its counted Overture clause taken out."""
    pro, sep, con = reasons.partition(" || ")
    kept = [p for p in pro.split(" | ") if not p.startswith("Overture: ")]
    return " | ".join(kept) + sep + con


@pytest.mark.golden
def test_objects_parity(mine, gold):
    """Every column but the four of fix 1 is exact, for every object."""
    exact = [c for c in gold.columns if c not in FIX_OVERTURE]
    bad = {c: int((gold[c] != mine[c]).sum()) for c in exact if (gold[c] != mine[c]).any()}
    assert not bad, f"columns with mismatches: {bad}"


@pytest.mark.golden
def test_overture_fsq_only_fix(mine, gold):
    """Fix 1 moves exactly the listed objects, exactly as the rule says, and no verdict."""
    changed = (gold[FIX_OVERTURE] != mine[FIX_OVERTURE]).any(axis=1)
    assert set(gold.loc[changed, "site_key"]) == OVERTURE_FSQ_ONLY
    assert changed.sum() == len(OVERTURE_FSQ_ONLY) == 21
    g, m = gold[changed], mine[changed]
    assert (g["ov_dataset"] == "Foursquare|Overture").all()
    assert (g["src_overture"] == "True").all() and (m["src_overture"] == "False").all()
    assert (m["n_sources"].astype(int) == g["n_sources"].astype(int) - 1).all()
    assert (m["sources"] == g["sources"].str.replace("; Overture", "", regex=False)).all()
    assert (m["reasons"] == g["reasons"].map(_without_overture)).all()
    assert (gold["confirmed"] == mine["confirmed"]).all()
    assert (m["n_sources"].astype(int) >= 2).all()


def _legend(ws) -> tuple[list[tuple], list[tuple]]:
    rows = list(ws.iter_rows(values_only=True))
    cut = rows.index(("Итоги", None))
    return rows[:cut], rows[cut:]


@pytest.mark.golden
def test_xlsx_parity(built, golden, gold):
    """Data sheets: exact, except fix 1's three cells on its objects' rows and the filled food column."""
    table, paths = built
    assert len(paths) == 3
    for mine_p in paths:
        gold_p = golden / "final_confirm" / mine_p.name
        gw, mw = openpyxl.load_workbook(gold_p), openpyxl.load_workbook(mine_p)
        assert gw.sheetnames == mw.sheetnames, mine_p.name
        gs, ms = gw["data"], mw["data"]
        assert (gs.max_row, gs.max_column) == (ms.max_row, ms.max_column), mine_p.name
        assert gs.auto_filter.ref == ms.auto_filter.ref and gs.freeze_panes == ms.freeze_panes
        g_rows, m_rows = list(gs.iter_rows(values_only=True)), list(ms.iter_rows(values_only=True))
        header = list(g_rows[0])
        assert list(m_rows[0]) == header, mine_p.name
        col = {h: header.index(h) for h in [FOOD_CELL, "Объект №", *FIX_OVERTURE_CELLS]}
        objs = table[table["file"] == mine_p.name.replace("_подтверждено.xlsx", ".xlsx")].reset_index(drop=True)
        fixed = set(objs.index[objs["site_key"].isin(OVERTURE_FSQ_ONLY)])
        diffs, food_rows = [], 0
        for i, (gr, mr) in enumerate(zip(g_rows[1:], m_rows[1:], strict=True), start=2):
            entity = gr[col["Объект №"]]
            assert gr[col[FOOD_CELL]] is None, "the archive's food column is empty"
            assert mr[col[FOOD_CELL]] == objs.at[entity, "food"] and mr[col[FOOD_CELL]], (mine_p.name, i)
            food_rows += 1
            if entity in fixed:   # fix 1's cells carry the decided values of the object
                assert [mr[col[h]] for h in FIX_OVERTURE_CELLS] == [
                    int(objs.at[entity, "n_sources"]), objs.at[entity, "sources"], objs.at[entity, "reasons"]]
            for j, (a, b) in enumerate(zip(gr, mr, strict=True)):
                if j == col[FOOD_CELL] or (entity in fixed and header[j] in FIX_OVERTURE_CELLS):
                    continue
                if a != b:
                    diffs.append((i, header[j], a, b))
        assert not diffs, f"{mine_p.name}: {len(diffs)} cells differ, first {diffs[:3]}"
        assert food_rows == len(g_rows) - 1
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
