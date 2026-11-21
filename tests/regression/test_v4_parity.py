"""The alcohol and tobacco pipelines on the fourth delivery, against the archive.

Runs both v4 deliveries through the workflow's steps -- input adapter, the
directory checks, the registry join over the whole match group (tobacco
first, as the archive stacked them), the category rule, the XLSX writer --
with the workflow's parquet round trips, and checks:

* the licence join through this path is the archive's `v4_licences.csv`,
  exactly (every match and licence field of all 1,694 records);
* our Yandex marks on the 440 alcohol rows the archive queried are its
  `v4_alcohol_none_yandex.csv` (mark, card name, card URL, distance, nearby,
  query URL);
* the new rules hold: a licence counts for alcohol only when it is the
  object's own, in force and name-agreeing; it never counts for tobacco and
  shows there only as the indirect `probable` / `weak` hint;
* the deliverable is the CSV's own columns with the verdict around them.

The confirmed counts themselves are new (no golden); they are cross-checked
against the licence statuses rather than pinned.
"""
from __future__ import annotations

import io
import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pandas as pd
import pytest
import yaml

from infracheck.categories import pipeline, registry
from infracheck.categories import spec as category_spec
from infracheck.checks import egais as chk
from infracheck.deliver import xlsx

ROOT = Path(__file__).resolve().parents[2]


def _text(df: pd.DataFrame) -> pd.DataFrame:
    buf = io.StringIO()
    df.to_csv(buf, sep=";", index=False)
    buf.seek(0)
    return pd.read_csv(buf, sep=";", dtype=str, keep_default_na=False)


def _trip(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    pipeline.write_parquet(df, path)
    return pipeline.read_parquet(path)


@pytest.fixture(scope="module")
def built(golden, tmp_path_factory):
    config = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    deliveries = pd.read_csv(ROOT / "config" / "deliveries.tsv", sep="\t", dtype=str)
    group = deliveries[deliveries["match_group"] == "v4"]
    assert list(group["category"]) == ["tobacco", "alcohol"], "the archive's order: tobacco first"
    out = tmp_path_factory.mktemp("v4")
    loaded = {d["delivery"]: pipeline.load(d, ROOT) for _, d in group.iterrows()}
    obs = {k: _trip(ob, out / f"{k}_objects.parquet") for k, (ob, _, _) in loaded.items()}
    cats = {d["delivery"]: config["categories"][d["category"]] for _, d in group.iterrows()}
    egais_policy = registry.policy("egais", cats["v4_alcohol"]["policy"]["egais"])
    old_agreement = {}
    original = chk.name_agrees

    def capture(ob, register, table, policy, brands, **kwargs):
        result = original(ob, register, table, policy, brands, **kwargs)
        if not kwargs.get("include_venue", True):
            old_agreement.update(zip(ob.index, result, strict=True))
        return result

    with patch.object(chk, "name_agrees", side_effect=capture):
        grp = _trip(pipeline.egais_group(list(obs.values()), egais_policy,
                                         golden / "egais" / "egais_licences_final.tsv"), out / "group.parquet")
    res = {}
    for _, d in group.iterrows():
        k, cat = d["delivery"], cats[d["delivery"]]
        spec, pols = category_spec(config, d["category"]), registry.policies(cat)
        evs = {}
        for c in spec.checks:
            if c == "egais":
                evs[c] = pipeline.split_group(grp, obs[k].index)
            else:
                path = pipeline.resource(c, config, cat)
                path = ROOT / path if path else None
                evs[c] = _trip(registry.run(c, obs[k], pols[c], path), out / f"{k}_{c}.parquet")
        table = _trip(pipeline.decide(obs[k], evs, spec), out / f"{k}_decided.parquet")
        dst = out / xlsx.output_name(d["path"], cat["deliver"])
        _, rowmap, raw = loaded[k]
        summary = pipeline.deliver(ROOT / d["path"], dst, raw, rowmap, table, spec, pols)
        res[k] = {"table": table, "evs": evs, "xlsx": dst, "raw": raw, "summary": dict(summary), "spec": spec}
    return {"group": grp, "objects": obs, "res": res, "old_agreement": old_agreement, "config": config}


@pytest.mark.golden
def test_licence_join_is_the_archives(built, golden):
    gold = pd.read_csv(golden / "v4_licences" / "v4_licences.csv", sep=";", encoding="utf-8-sig",
                       dtype=str, keep_default_na=False)
    index = pd.concat(list(built["objects"].values())).index
    w = chk.wide(built["group"], index)
    cols = chk.MATCH_FIELDS + list(chk.LICENCE_FIELDS)
    mine = _text(w[cols])
    assert len(mine) == len(gold) == 1694
    bad = {c: int((gold[c] != mine[c]).sum()) for c in cols if (gold[c] != mine[c]).any()}
    assert not bad, f"columns with mismatches: {bad}"


@pytest.mark.golden
def test_yandex_marks_are_the_archives(built, golden):
    gold = pd.read_csv(golden / "v4_yandex" / "v4_alcohol_none_yandex.csv", sep=None, engine="python",
                       encoding="utf-8-sig", dtype=str, keep_default_na=False).set_index("site_key")
    assert len(gold) == 440
    t = built["res"]["v4_alcohol"]["table"].set_index("site_key")
    mine = _text(t.reindex(gold.index)[["ya_status", "ya_name", "ya_url", "ya_distance_m", "ya_nearby",
                                        "ya_search_url"]].reset_index(drop=True))
    for g, m in [("yandex_status", "ya_status"), ("yandex_name", "ya_name"), ("yandex_url", "ya_url"),
                 ("yandex_distance_m", "ya_distance_m"), ("yandex_nearby", "ya_nearby"),
                 ("search_url", "ya_search_url")]:
        assert (gold[g].to_numpy() == mine[m].to_numpy()).all(), g


@pytest.mark.golden
def test_alcohol_licence_counts_only_with_the_name(built):
    r = built["res"]["v4_alcohol"]
    t, e = r["table"], r["evs"]["egais"].set_index("object_id")
    own = t["licence_status"].eq("Active")
    assert own.sum() == 350
    assert (t["src_egais"] == (own & t["licence_name_agrees"])).all()
    assert set(t.loc[t["src_egais"], "licence_name_basis"]) <= {"manual", "learned", "fuzzy"}
    assert not t.loc[t["licence_name_basis"].eq("generic"), "src_egais"].any()
    assert not t.loc[t["licence_status"].eq("Archived"), "src_egais"].any()
    weak = own & ~t["licence_name_agrees"]
    assert (e.loc[t.index[weak], "identity"] == "weak").all()
    assert t.loc[weak, "reasons"].str.contains(" || против/не найдено: ", regex=False).all()
    # Summaries report merged objects and source records separately. Only
    # record counts add up across trade types; one merged object can have both.
    s = r["summary"]
    records = []
    for trade in ("off-trade", "on-trade"):
        counts = re.fullmatch(r"(\d+) \(записей (\d+)\)", s[f"Подтверждено, {trade}"])
        assert counts is not None
        selected = t["confirmed"].astype(bool) & t["trade"].eq(trade)
        pooled = t["confirmed_object"].astype(bool) & t["trade"].eq(trade)
        assert int(counts[1]) == t.loc[pooled, "merged"].nunique()
        assert int(counts[2]) == int(selected.sum())
        records.append(int(counts[2]))
    assert sum(records) == int(t["confirmed"].sum())


@pytest.mark.golden
def test_tobacco_never_counts_the_registry(built):
    t = built["res"]["v4_tobacco"]["table"]
    assert not t["sources"].str.contains("ЕГАИС").any()
    assert "src_egais" not in t.columns
    assert not t["tobacco_reasons"].str.contains("ЕГАИС").any()
    probable = t["licence_evidence"].eq("probable")
    assert probable.sum() == 290
    assert (t.loc[probable, "reasons"].str.contains("|| против/не найдено: ", regex=False)).all()
    assert t.loc[probable, "reasons"].str.contains("продажу табака лицензия не подтверждает").all()
    assert not t.loc[~probable, "reasons"].str.contains("косвенный довод").any()


@pytest.mark.golden
def test_v4_deliverables(built):
    for k, r in built["res"].items():
        ws = openpyxl.load_workbook(r["xlsx"])["data"]
        header = [c.value for c in ws[1]]
        layout = r["spec"].raw["deliver"]
        front = [f["title"] for f in layout["front"]]
        appended = [a[0] for a in layout["appended"]]
        assert header == front + list(r["raw"].columns) + appended, k
        assert ws.max_row - 1 == len(r["raw"]) == len(r["table"])
        kind = [row[2] for row in ws.iter_rows(min_row=2, max_col=3, values_only=True)]
        assert all(kind) and set(kind) <= set(r["spec"].kind.words.values()), k
        assert "Подтверждение" in openpyxl.load_workbook(r["xlsx"]).sheetnames


@pytest.mark.golden
def test_pooled_verdicts_and_kind_transitions(built):
    for r in built["res"].values():
        t, spec = r["table"], r["spec"]
        groups = t[[g.column for g in spec.groups]].groupby(t["merged"]).any()
        expected = groups.sum(axis=1).ge(spec.min_sources).astype(int)
        assert t["confirmed_object"].tolist() == t["merged"].map(expected).tolist()
        assert spec.kind.vote_identity == ("strong",)
        # Both v4 categories already used strong-only voting before this change.
        from infracheck.categories import base

        old = base.kind(registry.stack(r["evs"]), t.index, spec)[spec.kind.column]
        transitions = pd.crosstab(old, t[spec.kind.column])
        assert int(transitions.to_numpy().sum() - sum(transitions.loc[x, x] for x in transitions.index
                                                       if x in transitions.columns)) == 0


@pytest.mark.golden
def test_venue_name_changes_exactly_the_expected_licences(built):
    t = built["res"]["v4_alcohol"]["table"]
    old = t.index.map(built["old_agreement"]).to_numpy(dtype=bool)
    new = t["licence_name_agrees"].to_numpy()
    assert not (old & ~new).any()
    tobacco = built["res"]["v4_tobacco"]["table"]
    assert tobacco["licence_name_agrees"].tolist() == [built["old_agreement"][i] for i in tobacco.index]
    changed = t.loc[~old & new]
    assert set(changed.loc[changed["licence_status"].eq("Active"), "name"]) == {
        "Шафран, кафе", "Stare Mesto", "Кушать подано", "Почтовая контора 1786 г.",
        "Боброфф", "Миндаль", "У фонаря",
    }
    assert len(changed) == 12  # Seven current, five archived; the latter cannot count.
    for trade, before, after in [("off-trade", (218, 51), (218, 51)),
                                 ("on-trade", (11, 70), (18, 63))]:
        mask = t["licence_status"].eq("Active") & t["trade"].eq(trade)
        assert (int(old[mask].sum()), int((~old[mask]).sum())) == before
        assert (int(new[mask].sum()), int((~new[mask]).sum())) == after


@pytest.mark.golden
def test_alcohol_ladder_transitions_and_summary(built):
    from infracheck.categories import base

    config = deepcopy(built["config"])
    ladder = config["categories"]["alcohol"]["kind"]["schemes"]["ru_text"]["ladder"]
    ladder[1][1] = ladder[1][1].replace("(?<!без)алкогол|алко(?!гол)", "алкогол|алко").replace(
        "ароматный мир|beer", "ароматный мир|ресторан|банкет|beer")
    ladder[0][1] = ladder[0][1].replace(r"\b", "")
    ladder[2][1] = ladder[2][1].replace(r"\b", "")
    old_spec = category_spec(config, "alcohol")
    r = built["res"]["v4_alcohol"]
    t = r["table"]
    old = base.kind(registry.stack(r["evs"]), t.index, old_spec)["alcohol"]
    transitions = pd.crosstab(old, t["alcohol"])
    changed = {(a, b): int(transitions.loc[a, b]) for a in transitions.index for b in transitions.columns
               if a != b and transitions.loc[a, b]}
    assert changed == {("да", "нет данных"): 20, ("противоречие", "да"): 1}
    s = r["summary"]
    for typ, records in [("Direct", 179), ("Inferred", 766)]:
        assert s[f"Объектов, тип подрядчика {typ}"].endswith(f"(записей {records})")
        selected = t["evidence"].eq(typ) & t["confirmed"].astype(bool)
        pooled = t["evidence"].eq(typ) & t["confirmed_object"].astype(bool)
        assert s[f"Подтверждено, тип подрядчика {typ}"] == (
            f"{t.loc[pooled, 'merged'].nunique()} (записей {int(selected.sum())})")
    wb = openpyxl.load_workbook(r["xlsx"])
    legend = " ".join(str(c.value) for row in wb["Подтверждение"] for c in row if c.value is not None)
    assert "922 из 945 записей (97,6%)" in legend
    assert "в файле нет двух строк" not in legend
    tobacco = openpyxl.load_workbook(built["res"]["v4_tobacco"]["xlsx"])["data"]
    headers = [c.value for c in tobacco[1]]
    assert "Алкогольная лицензия: статус" in headers
    assert "Алкогольная лицензия: владелец" in headers
    assert "ЕГАИС: статус" not in headers and "ЕГАИС: владелец" not in headers
