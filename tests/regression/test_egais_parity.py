"""The registry check against the archive's outputs, cell for cell.

Three golden files, one per thing ported:

* `final_egais/final_egais.csv` -- `checks.egais.run_catering` + `wide`, every
  `egais_*` column of all 1,688 final-delivery objects.  The objects are the
  ones the archive passed to `match_catering` (city, name, address, lat, lon of
  `final_confirm_objects.csv`, row-aligned with `final_egais.csv`).
* `v4_licences/v4_licences.csv` and `v4_brand_aliases.csv` -- `run_v4` at the
  archive's default flags (every rule on).
* `egais/egais_licences_*.tsv` -- the parser.  A full pass over the ZIP takes
  ~6 minutes, so the test compares the TSV the CLI left in `data/temp/egais/`
  and is skipped when it is not there:

      pixi run python -m infracheck.registers.egais parse --zip resources/egais/<zip> \\
          --settlements архангельск северодвинск новодвинск \\
          --out data/temp/egais/egais_licences_final.tsv

Both sides are written to CSV and read back as raw strings, so a cell agrees
exactly when the file the archive wrote would be the same.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from infracheck.checks import egais as chk
from infracheck.inputs import v4_csv
from infracheck.registers import egais as reg

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.golden


def _strings(df: pd.DataFrame, **kw) -> pd.DataFrame:
    buf = io.StringIO()
    df.to_csv(buf, **kw)
    buf.seek(0)
    return pd.read_csv(buf, dtype=str, keep_default_na=False, sep=kw.get("sep", ","))


def _mismatches(mine: pd.DataFrame, gold: pd.DataFrame, cols) -> dict[str, int]:
    assert len(mine) == len(gold)
    return {c: int((mine[c].to_numpy() != gold[c].to_numpy()).sum()) for c in cols
            if (mine[c].to_numpy() != gold[c].to_numpy()).any()}


def test_final_catering(golden):
    gold = pd.read_csv(golden / "final_egais/final_egais.csv", encoding="utf-8-sig",
                       dtype=str, keep_default_na=False)
    o = pd.read_csv(golden / "final_confirm/final_confirm_objects.csv", sep=";",
                    encoding="utf-8-sig")
    ob = pd.DataFrame({"city": o["city"], "name": o["name"].fillna("").astype(str),
                       "address": o["address"].fillna("").astype(str),
                       "lat": o["lat"], "lon": o["lon"]})
    ob.index = pd.Index(gold["object_id"], name="object_id")
    register = reg.to_register(reg.load(golden / "egais/egais_licences_final.tsv"))
    evidence = chk.run_catering(ob, register)
    mine = _strings(chk.wide(evidence))
    assert list(mine["object_id"]) == list(gold["object_id"])
    assert _mismatches(mine, gold, chk.OUT_COLUMNS) == {}

    # Counted rows carry the archive's «Основания» clause; the rest its note.
    e = evidence.set_index("object_id")
    own = e["egais_match"].astype(bool)
    assert int(own.sum()) == 58
    assert set(e.loc[own, "identity"]) == {"strong"} and set(e.loc[own, "record_state"]) == {"current"}
    g = gold.set_index("object_id")
    want = ("лицензия " + g["egais_licence_no"] + " (" + g["egais_status"] + "), "
            + g["egais_holder"] + ", " + g["egais_outlet_address"])
    assert (e.loc[own, "text"] == want[own.to_numpy()]).all()
    assert (e.loc[~own, "text"] == g.loc[~own.to_numpy(), "egais_note"]).all()

    # record_state is the cited licence's own state, whatever the link.
    cited = g["egais_licence_no"] != ""
    lapsed = cited & (g["egais_status"].str.strip().str.lower() != "действующая")
    assert (e.loc[lapsed.to_numpy(), "record_state"] == "inactive").all()
    assert (e.loc[(cited & ~lapsed).to_numpy(), "record_state"] == "current").all()
    assert (e.loc[(~cited).to_numpy(), "record_state"] == "unknown").all()
    ns = e.loc[own, "name_score"].astype(float)
    assert ns.notna().all() and ns.between(0, 100).all() and ns.max() > 1

    other = e["egais_note"].str.startswith("по адресу только недействующие")
    assert other.any() and set(e.loc[other, "identity"]) == {"weak"}
    assert set(e.loc[other, "record_state"]) == {"inactive"}


@pytest.fixture(scope="module")
def v4_run(golden):
    v4 = v4_csv.load(ROOT / "data/raw/v4")
    register = reg.to_register(reg.load(golden / "egais/egais_licences_arkhangelsk.tsv"))
    return chk.run_v4(v4, register)


def test_v4_licences(golden, v4_run):
    gold = pd.read_csv(golden / "v4_licences/v4_licences.csv", sep=";", encoding="utf-8-sig",
                       dtype=str, keep_default_na=False)
    mine = _strings(v4_run.table, sep=";", index=False)
    assert _mismatches(mine, gold, chk.MATCH_FIELDS + list(chk.LICENCE_FIELDS)) == {}
    assert mine["licence_status"].value_counts().to_dict() == gold["licence_status"].value_counts().to_dict()


def test_v4_evidence(v4_run):
    e, t = v4_run.evidence, v4_run.table
    assert len(e) == len(t)
    assert (e.loc[(t["licence_status"] == "Archived").to_numpy(), "record_state"] == "inactive").all()
    assert (e.loc[(t["licence_status"] == "Active").to_numpy(), "record_state"] == "current").all()


def test_v4_brand_aliases(golden, v4_run):
    gold = pd.read_csv(golden / "v4_licences/v4_brand_aliases.csv", sep=";", encoding="utf-8-sig",
                       dtype=str, keep_default_na=False)
    mine = _strings(v4_run.brands.table, sep=";", index=False)
    assert list(mine.columns) == list(gold.columns)
    assert _mismatches(mine, gold, gold.columns) == {}


def test_parser(golden):
    parsed = ROOT / "data/temp/egais/egais_licences_final.tsv"
    if not parsed.exists():
        pytest.skip(f"{parsed.relative_to(ROOT)} absent: run the parse CLI first (~6 min)")
    assert parsed.read_bytes() == (golden / "egais/egais_licences_final.tsv").read_bytes()
    # Its Arkhangelsk rows are the city extract the v4 join reads.
    f = pd.read_csv(parsed, sep="\t", dtype=str, keep_default_na=False)
    city = f[f["outlet_settlement"].map(reg.settlement_name) == "архангельск"].reset_index(drop=True)
    a = pd.read_csv(golden / "egais/egais_licences_arkhangelsk.tsv", sep="\t", dtype=str,
                    keep_default_na=False)
    pd.testing.assert_frame_equal(city, a)
