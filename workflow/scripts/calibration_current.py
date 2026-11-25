"""Sample-B metrics for the verdicts as they stand now, and for the unified base.

The auditor's labels on sample B describe the ground (does the object exist, is
it open, is it of its kind); they do not depend on the pipeline's rules. The
sampling design (strata, allocation, weights) is fixed when the sample is
drawn. So when the rules move after the evaluation, the same labels and the
same weights evaluate the new verdicts without a single new query: only the
pipeline's side of each pair is replaced.

Two verdicts are evaluated, each with the unchanged estimator of
`infracheck.audit.evaluate` (Manski bounds, Wilson CIs, sensitivity analyses):

- ``current``: «Подтверждено (объект)» and «Вид?» of the current run;
- ``base``: «В базу» of the unified base (confirmed, kind not «нет», not read
  closed by our Yandex query) -- what the supervisor receives as the base.
  Its «PPV existence» is the share of base objects that exist.

The unit of sample B is the merged object («Объект (склеен)»), the same unit
as a row of the base (`ID` = delivery:merged). A sampled unit missing from the
base is an error, not a skip.

The base is built here from `results/finalest/` with `unified_base.py` (the
same call as `pixi run deliverable`), into `calibration/base_snapshot.xlsx`;
`verdicts_now.csv` keeps each sampled unit's flags, and `build_deliverable.py`
refuses to build when its own base disagrees with them (stale metrics).

Usage: pixi run calibration-current [--base PATH] [--bootstrap N]
Writes results/finalest/calibration/{current,base}/ and verdicts_now.csv.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pandas as pd

from infracheck.audit.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT / "results" / "finalest" / "calibration"
SNAPSHOT = CAL / "base_snapshot.xlsx"
UNIFIED_BASE = ROOT / "workflow" / "scripts" / "unified_base.py"
DELIVERIES = ROOT / "config" / "deliveries_finalest.tsv"


def build_snapshot(path: Path) -> Path:
    spec = importlib.util.spec_from_file_location("unified_base", UNIFIED_BASE)
    ub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ub)
    ub.main(["--run", str(ROOT / "results" / "finalest"), "--deliveries", str(DELIVERIES), "--out", str(path)])
    return path


def verdicts(sample: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    b = base.set_index("ID")
    missing = set(sample["unit_id"]) - set(b.index)
    if missing:
        raise RuntimeError(f"{len(missing)} sampled units are not rows of the base, e.g. {sorted(missing)[:3]}")
    out = sample[["unit_id", "category", "city", "stratum", "ver_exists", "ver_kind"]].copy()
    out = out.rename(columns={"ver_exists": "ver_exists_sampled", "ver_kind": "ver_kind_sampled"})
    row = b.loc[out["unit_id"]]
    out["confirmed_now"] = row["Подтверждено"].astype(int).to_numpy()
    out["kind_now"] = row["Вид?"].fillna("нет данных").astype(str).to_numpy()
    out["in_base"] = row["В базу"].astype(int).to_numpy()
    out["why_not_in_base"] = row["Почему не в базе"].fillna("").astype(str).to_numpy()
    return out


def run(variant: str, sample: pd.DataFrame, v: pd.DataFrame, bootstrap: int) -> None:
    s = sample.copy()
    flag = v["confirmed_now"] if variant == "current" else v["in_base"]
    s["ver_exists"] = flag.map({1: "conf", 0: "not"}).to_numpy()
    s["ver_kind"] = v["kind_now"].to_numpy()
    path = CAL / variant / "sample_B.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    s.to_csv(path, index=False)
    evaluate(
        path,
        CAL / "allocation.tsv",
        CAL / "auditor_labels_B.csv",
        CAL / variant,
        None,
        bootstrap,
        CAL / "auditor_input_B.csv",
        CAL / "diagnostics" / "osm_gis_merge.csv",
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", type=Path, help="a built base (default: build a snapshot of results/finalest)")
    ap.add_argument("--bootstrap", type=int, default=4000)
    args = ap.parse_args(argv)
    sample = pd.read_csv(CAL / "sample_B.csv").fillna({"ver_exists": "", "ver_kind": ""})
    base = pd.read_excel(args.base or build_snapshot(SNAPSHOT), sheet_name="База")
    v = verdicts(sample, base)
    v.to_csv(CAL / "verdicts_now.csv", index=False, encoding="utf-8-sig")
    moved = v[(v["ver_exists_sampled"].eq("conf")).astype(int) != v["confirmed_now"]]
    print(f"sample B: {len(v)} units; verdict moved since sampling: {len(moved)}; in base: {v['in_base'].sum()}")
    for variant in ("current", "base"):
        run(variant, sample, v, args.bootstrap)
        print(f"wrote {CAL / variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
