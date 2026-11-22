"""Internal-validity evaluator: labels, weights, bounds and variance on hand-computed cases."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "workflow" / "scripts" / "internal_validity_eval.py"
spec = importlib.util.spec_from_file_location("internal_validity_eval", SCRIPT)
ive = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ive  # dataclasses resolve annotations through sys.modules
spec.loader.exec_module(ive)


def test_error_label_by_metric():
    assert ive.error_label("false_accept", "no", "open") == 1
    assert ive.error_label("false_accept", "yes", "closed") == 0
    assert np.isnan(ive.error_label("false_accept", "unclear", "open"))
    assert ive.error_label("false_reject", "yes", "unclear") == 1
    assert ive.error_label("false_reject", "no", "open") == 0
    # a rejected record the judge calls closed would not have counted anyway
    assert ive.error_label("false_reject_open", "yes", "closed") == 0
    assert ive.error_label("false_reject_open", "yes", "unclear") == 1
    assert np.isnan(ive.error_label("false_reject_open", "unclear", "open"))
    # state strata: right only when the same organisation and the record says closed
    assert ive.error_label("state_error", "yes", "closed") == 0
    assert ive.error_label("state_error", "yes", "open") == 1
    assert ive.error_label("state_error", "no", "closed") == 1
    assert np.isnan(ive.error_label("state_error", "yes", "unclear"))


def test_consensus_needs_agreement_of_two_resolved_labels():
    a = pd.Series([1.0, 0.0, 1.0, np.nan, np.nan])
    b = pd.Series([1.0, 0.0, 0.0, 1.0, np.nan])
    got = ive.consensus(a, b)
    assert got.iloc[:2].tolist() == [1.0, 0.0]
    assert got.iloc[2:].isna().all()


def _two_strata():
    # stratum A: N = 100, 4 sampled: error, ok, unresolved, ok
    # stratum B: N = 10, 2 sampled: error, error
    return pd.DataFrame(
        {
            "stratum": ["A"] * 4 + ["B"] * 2,
            "pop_n": [100] * 4 + [10] * 2,
            "err": [1.0, 0.0, np.nan, 0.0, 1.0, 1.0],
        }
    )


def test_point_and_bounds_are_population_weighted():
    t = ive.stratum_table(_two_strata(), "err")
    assert t.loc["A"].tolist() == [100, 4, 3, 1]
    # resolved-only: A 1/3, B 2/2, weighted by N
    assert ive.point_estimate(t) == pytest.approx((100 / 3 + 10) / 110)
    lo, hi = ive.bounds(t)
    assert lo == pytest.approx((100 * 1 / 4 + 10 * 2 / 2) / 110)  # unresolved counted as right
    assert hi == pytest.approx((100 * 2 / 4 + 10 * 2 / 2) / 110)  # unresolved counted as wrong
    # an unweighted share would be 3/5 = 0.6: the weights must matter
    assert ive.point_estimate(t) != pytest.approx(0.6)


def test_kish_effective_size_reflects_unequal_weights():
    t = ive.stratum_table(_two_strata(), "err")
    # per resolved label: A 100/3 (3 labels), B 10/2 (2 labels)
    w = np.array([100 / 3] * 3 + [5.0] * 2)
    assert ive.kish_neff(t) == pytest.approx(w.sum() ** 2 / (w**2).sum())
    assert ive.kish_neff(t) < 5  # never above the resolved count


def test_estimate_flags_small_domains():
    rng = np.random.default_rng(0)
    e = ive.estimate(_two_strata(), "err", rng)
    assert (e.n, e.resolved, e.bad) == (6, 5, 3)
    assert not e.sufficient
    assert e.unresolved_share == pytest.approx(100 * 0.25 / 110)
    assert e.lo95 <= e.point <= e.hi95


def _write_sample(iv: Path, weight_b: float):
    manifest = {
        "n_decisions": 3,
        "n_packets": 1,
        "strata": {
            "yandex_accepted": {"population": 20, "sample": 2},
            "yandex_rejected_near": {"population": 5, "sample": 1},
        },
    }
    (iv / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pd.DataFrame(
        {
            "decision_id": ["IV-a", "IV-b", "IV-c"],
            "stratum": ["yandex_accepted", "yandex_accepted", "yandex_rejected_near"],
            "source": ["yandex"] * 3,
            "pipeline_decision": ["accepted", "accepted", "rejected"],
            "weight": [10.0, 10.0, weight_b],
            "rank_in_stratum": [1, 2, 1],
            "candidate_name": ["X", "Y", "Z"],
            "distance_m": [1.0, 2.0, 3.0],
            "city": ["Архангельск"] * 3,
        }
    ).to_csv(iv / "decisions.csv", index=False, encoding="utf-8-sig")
    pk = [
        {"decision_id": i, "object": {"name": n, "address": ""}}
        for i, n in [("IV-a", "A"), ("IV-b", "B"), ("IV-c", "C")]
    ]
    (iv / "packets_1.jsonl").write_text("\n".join(json.dumps(p) for p in pk), encoding="utf-8")
    j1 = [("IV-a", "no", "open"), ("IV-b", "yes", "open"), ("IV-c", "yes", "unclear")]
    j2 = [("IV-a", "no", "open"), ("IV-b", "unclear", "open"), ("IV-c", "yes", "unclear")]
    for name, rows in (("judgments_1.jsonl", j1), ("judgments2_12.jsonl", j2)):
        (iv / name).write_text(
            "\n".join(json.dumps({"decision_id": i, "same_org": s, "open": o, "reason": "r"}) for i, s, o in rows),
            encoding="utf-8",
        )
    for name in ("judgments_2.jsonl", "judgments_3.jsonl", "judgments_4.jsonl", "judgments2_34.jsonl"):
        (iv / name).write_text("", encoding="utf-8")


def test_load_takes_weights_from_the_manifest(tmp_path):
    _write_sample(tmp_path, weight_b=5.0)
    dec = ive.load(tmp_path).set_index("decision_id")
    assert dec.loc["IV-a", "pop_n"] == 20 and dec.loc["IV-c", "design_n"] == 1
    assert dec.loc["IV-a", "err_false_accept_consensus"] == 1
    assert np.isnan(dec.loc["IV-b", "err_false_accept_consensus"])  # one judge unclear
    assert dec.loc["IV-c", "err_false_reject_consensus"] == 1
    assert np.isnan(dec.loc["IV-a", "err_false_reject_consensus"])  # not a rejected stratum
    m = ive.metrics(dec.reset_index())
    row = m[(m["reference"] == "consensus") & (m["source"] == "yandex") & (m["metric"] == "false_accept")].iloc[0]
    assert (row["bound_lo"], row["bound_hi"]) == (0.5, 1.0)
    assert not row["sufficient"] and np.isnan(row["point"])  # < 10 resolved: counts only


def test_load_refuses_weights_that_disagree_with_the_manifest(tmp_path):
    _write_sample(tmp_path, weight_b=4.0)
    with pytest.raises(RuntimeError, match="weights disagree"):
        ive.load(tmp_path)
