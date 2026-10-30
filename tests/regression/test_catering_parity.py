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

And for the two changes of 2026-10-30
(docs/changes/2026-10-30-merged-objects-and-kind-votes.md):

* a record another object won (`identity = weak`) no longer votes on
  «Общепит?»: `food_reasons` loses exactly those clauses, on exactly the
  objects that had one, and `food` moves only where the fold says so;
* one sign recorded twice is one object: the new `merged` column is the
  connected component of relink links, «Объект (склеен)» is appended to the
  data sheet, and the «Итоги» count merged objects with the archive's record
  counts in brackets -- every record count but the «Общепит?» ones (moved by
  the vote change above) is the archive's.

The registry extract is the golden one (`resources/golden/egais/`), which the
`egais_register` rule reproduces (tests/regression/test_egais_parity.py);
parsing the 10.9 GB ZIP here would cost six minutes and test nothing new.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
import yaml
from openpyxl.utils import get_column_letter

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
# 2026-10-30, weak records no longer vote on the kind.
FIX_KIND = ["food", "food_reasons"]
FIX_KIND_CELLS = ["Общепит?", "Общепит: основания"]
KIND_CHANGED_REASONS = 11      # objects whose «Общепит: основания» lose a clause
KIND_TRANSITIONS = {("да", "нет данных"): 1}
WEAK_KIND_SCHEMES = ("fsq_category", "overture_category")
# 2026-10-30, merged objects: (merged objects, confirmed merged) per file; the records are the archive's.
MERGED = {"архангельск": (1066, 728), "северодвинск": (431, 337), "новодвинск": (75, 45)}
MERGED_CELL = "Объект (склеен)"


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
    return table.assign(file=Path(d["path"]).name, _evidence=[registry.stack(evs)] * len(table)), dst


@pytest.fixture(scope="module")
def built(golden, tmp_path_factory):
    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    deliveries = pd.read_csv(ROOT / "config" / "deliveries.tsv", sep="	", dtype=str)
    deliveries = deliveries[deliveries["category"] == catering.NAME]
    out = tmp_path_factory.mktemp("catering")
    register = golden / "egais" / "egais_licences_final.tsv"
    runs = [_run(config, d, register, out) for _, d in deliveries.iterrows()]
    evs = pd.concat([t["_evidence"].iloc[0] for t, _ in runs], ignore_index=True)
    tables = [t.drop(columns="_evidence").reset_index() for t, _ in runs]
    return pd.concat(tables, ignore_index=True), [p for _, p in runs], evs


@pytest.fixture(scope="module")
def gold(golden) -> pd.DataFrame:
    return pd.read_csv(golden / "final_confirm" / "final_confirm_objects.csv", sep=";",
                       encoding="utf-8-sig", dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def mine(built, gold) -> pd.DataFrame:
    table, _, _ = built
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
    """Every column but the four of fix 1 and the two of the kind vote is exact, for every object."""
    exact = [c for c in gold.columns if c not in FIX_OVERTURE + FIX_KIND]
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


def _weak_kind_clauses(evs: pd.DataFrame) -> dict[str, list[str]]:
    """Per object (site_key), the «Общепит: основания» clause heads of its weak FSQ / Overture rows."""
    w = evs[evs["identity"].eq("weak") & evs["kind_scheme"].isin(WEAK_KIND_SCHEMES) & evs["kind_text"].ne("")]
    out: dict[str, list[str]] = {}
    for r in w.itertuples():
        out.setdefault(r.object_id, []).append(f"{r.label}: «{r.kind_text}» → ")
    return out


@pytest.mark.golden
def test_weak_records_no_longer_vote_on_kind(built, mine, gold):
    """«Общепит?» moves only by the weak clauses dropped; the fold of what is left decides."""
    table, _, evs = built
    weak = _weak_kind_clauses(evs)
    key = dict(zip(table["object_id"], table["site_key"], strict=True))
    weak_keys = {key[o]: heads for o, heads in weak.items()}
    changed = gold["food_reasons"] != mine["food_reasons"]
    assert int(changed.sum()) == KIND_CHANGED_REASONS
    for i in gold.index:
        heads = weak_keys.get(gold.at[i, "site_key"], [])
        kept = [c for c in gold.at[i, "food_reasons"].split(" | ")
                if c and not any(c.startswith(h) for h in heads)]
        assert mine.at[i, "food_reasons"] == " | ".join(kept), gold.at[i, "site_key"]
    moved = gold["food"] != mine["food"]
    pairs = pd.Series(list(zip(gold.loc[moved, "food"], mine.loc[moved, "food"]))).value_counts().to_dict()
    assert pairs == KIND_TRANSITIONS
    assert not (moved & ~changed).any()


@pytest.mark.golden
def test_merged_objects(built):
    """`merged` is the relink components; records of one merged object never disagree on «Подтверждено»."""
    table, _, evs = built
    for f, part in table.groupby("file"):
        city = next(c for c in MERGED if c in f)
        n, conf = MERGED[city]
        assert part["merged"].nunique() == n, f
        assert part.loc[part["confirmed"].astype(bool), "merged"].nunique() == conf, f
        assert (part.groupby("merged")["confirmed"].nunique() == 1).all(), f
        # The merged id is the lowest «Объект №» of the component.
        assert (part.groupby("merged")["entity"].min() == part.groupby("merged")["entity"].min().index).all(), f
    # Every relink link joins two records of one merged object, and no other records are joined.
    rl = evs[evs["check"].eq("relink")]
    m = table.set_index("object_id")["merged"]
    assert (m[rl["object_id"]].to_numpy() == m[rl["relink_object"]].to_numpy()).all()
    pairs = {tuple(sorted(p)) for p in zip(rl["object_id"], rl["relink_object"], strict=True)}
    sizes = table.groupby(["file", "merged"]).size()
    assert set(sizes) == {1, 2} and int((sizes == 2).sum()) == len(pairs) == 116


def _legend(ws) -> tuple[list[tuple], list[tuple]]:
    rows = list(ws.iter_rows(values_only=True))
    cut = rows.index(("Итоги", None))
    return rows[:cut], rows[cut:]


def _merged_line(label: str, gold_v, mine_v, n_merged: int, n_conf: int, food: pd.Series) -> None:
    """One «Итоги» line: the archive's record count in brackets, the merged count before it."""
    rec = f"{FOOD_CELL} — "
    if label in ("Строк", "Подтверждено строк"):
        assert mine_v == gold_v, label
    elif label.startswith("Объектов"):
        assert mine_v == f"{n_merged} (записей {gold_v})", label
    elif label == "Подтверждено объектов":
        records = str(gold_v).rsplit(" (", 1)[0]   # "803 из 1141 (70.4%)" -> "803 из 1141"
        assert mine_v == f"{n_conf} из {n_merged} ({n_conf / n_merged:.1%}); записей {records}", label
    else:
        m = re.fullmatch(r"(\d+(?: / \d+)*) \(записей (.+)\)", str(mine_v))
        assert m, (label, mine_v)
        if label.startswith(rec):   # the kind vote moved these record counts
            word = label[len(rec):].removesuffix(" (объектов)")
            assert m.group(2) == str(int((food == word).sum())), label
        else:
            assert m.group(2) == str(gold_v), label
        assert all(int(x) <= int(y) for x, y in zip(m.group(1).split(" / "), m.group(2).split(" / "),
                                                    strict=True)), label


@pytest.mark.golden
def test_xlsx_parity(built, golden, gold):
    """Data sheets: exact, except the cells the 2026-10-30 changes move, each checked against the decided value.

    Fix 1's three cells on its objects' rows, the filled food column, «Общепит?» /
    «Общепит: основания» where the kind vote moved them, and the appended
    «Объект (склеен)»; the «Итоги» carry merged counts beside the archive's.
    """
    table, paths, _ = built
    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    what_is = dict(config["categories"]["catering"]["deliver"]["legend"])["Что такое объект"]
    assert len(paths) == 3
    for mine_p in paths:
        gold_p = golden / "final_confirm" / mine_p.name
        gw, mw = openpyxl.load_workbook(gold_p), openpyxl.load_workbook(mine_p)
        assert gw.sheetnames == mw.sheetnames, mine_p.name
        gs, ms = gw["data"], mw["data"]
        assert (gs.max_row, gs.max_column + 1) == (ms.max_row, ms.max_column), mine_p.name
        last = get_column_letter(gs.max_column + 1)
        assert ms.auto_filter.ref == f"A1:{last}{gs.max_row}" and gs.freeze_panes == ms.freeze_panes
        g_rows, m_rows = list(gs.iter_rows(values_only=True)), list(ms.iter_rows(values_only=True))
        header = list(g_rows[0])
        assert list(m_rows[0]) == [*header, MERGED_CELL], mine_p.name
        col = {h: header.index(h) for h in [FOOD_CELL, "Объект №", *FIX_OVERTURE_CELLS, *FIX_KIND_CELLS]}
        file = mine_p.name.replace("_подтверждено.xlsx", ".xlsx")
        objs = table[table["file"] == file].reset_index(drop=True)
        fixed = set(objs.index[objs["site_key"].isin(OVERTURE_FSQ_ONLY)])
        g_reasons = gold.set_index("site_key")["food_reasons"]
        kind_moved = set(objs.index[objs["food_reasons"].to_numpy() != g_reasons[objs["site_key"]].to_numpy()])
        diffs, food_rows = [], 0
        for i, (gr, mr) in enumerate(zip(g_rows[1:], m_rows[1:], strict=True), start=2):
            entity = gr[col["Объект №"]]
            assert gr[col[FOOD_CELL]] is None, "the archive's food column is empty"
            assert mr[col[FOOD_CELL]] == objs.at[entity, "food"] and mr[col[FOOD_CELL]], (mine_p.name, i)
            assert mr[-1] == objs.at[entity, "merged"], (mine_p.name, i)
            food_rows += 1
            if entity in fixed:   # fix 1's cells carry the decided values of the object
                assert [mr[col[h]] for h in FIX_OVERTURE_CELLS] == [
                    int(objs.at[entity, "n_sources"]), objs.at[entity, "sources"], objs.at[entity, "reasons"]]
            if entity in kind_moved:
                assert mr[col["Общепит: основания"]] == (objs.at[entity, "food_reasons"] or None)
            for j, (a, b) in enumerate(zip(gr, mr[:-1], strict=True)):
                if (j == col[FOOD_CELL] or (entity in fixed and header[j] in FIX_OVERTURE_CELLS)
                        or (entity in kind_moved and header[j] == "Общепит: основания")):
                    continue
                if a != b:
                    diffs.append((i, header[j], a, b))
        assert not diffs, f"{mine_p.name}: {len(diffs)} cells differ, first {diffs[:3]}"
        assert food_rows == len(g_rows) - 1
        g_how, g_sum = _legend(gw["Подтверждение"])
        m_how, m_sum = _legend(mw["Подтверждение"])
        assert [k for k, _ in g_sum] == [k for k, _ in m_sum], mine_p.name
        city = next(c for c in MERGED if c in file)
        for (label, gv), (_, mv) in zip(g_sum[1:], m_sum[1:], strict=True):
            _merged_line(label, gv, mv, *MERGED[city], objs["food"])

        def moved(v):
            if not isinstance(v, str):
                return v
            for old, new in LEGEND_PATHS.items():
                v = v.replace(old, new)
            return v
        g_how = [tuple(moved(v) for v in r) for r in g_how]
        assert [r for r in g_how if r[0] != "Что такое объект"] == [r for r in m_how if r[0] != "Что такое объект"]
        assert ("Что такое объект", what_is) in m_how, mine_p.name
