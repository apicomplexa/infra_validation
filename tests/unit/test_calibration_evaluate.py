import numpy as np
import pandas as pd
import pytest

from infracheck.audit.evaluate import (
    add_reference,
    cohen_kappa,
    confusion,
    evaluate,
    human_kappa,
    metric_rows,
    variance_groups,
    weighted_ratio,
)
from infracheck.audit.human_check import HEADERS


def _known_data():
    sample = pd.DataFrame(
        {
            "unit_id": ["a", "b", "c", "d"],
            "delivery": ["d"] * 4,
            "category": ["catering"] * 4,
            "stratum": ["S1"] * 4,
            "population_N": [8] * 4,
            "weight": [2.0] * 4,
            "ver_exists": ["conf", "conf", "not", "not"],
            "ver_kind": ["да", "нет", "автомат", "нет данных"],
        }
    )
    labels = pd.DataFrame(
        {
            "unit_id": ["a", "b", "c", "d"],
            "exists": ["yes", "yes", "no", "yes"],
            "open": ["open", "closed", "closed", "open"],
            "category_ok": ["yes", "no", "yes", "unclear"],
            "is_vending": ["no", "no", "yes", "no"],
        }
    )
    return add_reference(sample, labels)


def test_known_stratum_weighted_rates_and_unclear_bounds():
    data = _known_data()
    # Exact design truth in this census-like synthetic frame: two real, two not.
    p_e = weighted_ratio(data.assign(y=data.E, one=1.0), "y", "one", bootstrap=50)
    assert p_e["estimate"] == pytest.approx(0.5)
    rows = metric_rows(data, bootstrap=50)
    precision = next(row for row in rows if row["metric"] == "PPV existence")
    assert precision["estimate"] == pytest.approx(0.5)
    assert precision["unclear_low"] <= precision["estimate"] <= precision["unclear_high"]
    assert data.loc[data.unit_id.eq("b"), "K"].iloc[0] == 0  # Closed, but identified: K remains defined.
    wrong_kind = next(row for row in rows if row["metric"] == "wrong kind prevalence")
    assert wrong_kind["estimate"] == pytest.approx(0.5)


def test_weighted_ratio_renormalizes_resolved_labels_within_stratum():
    data = _known_data()
    data.loc[0, "E"] = np.nan
    data["one"] = 1.0
    result = weighted_ratio(data.assign(y=data.E), "y", "one", bootstrap=30)
    assert result["estimate"] == pytest.approx(1 / 3)
    assert np.isfinite(result["low"])


def test_kappa_and_confusion_include_unclear_cell():
    assert cohen_kappa(["yes", "no", "yes"], ["yes", "yes", "yes"]) == pytest.approx(0)
    data = _known_data()
    rows = confusion(data, "ver_exists", "E", ["conf", "not"], [0.0, 1.0, "?"])
    assert len(rows) == 6
    assert sum(row["sample_n"] for row in rows) == 4
    assert sum(row["sample_n"] for row in rows if row["auditor"] == "?") == 0


def test_ppv_numerator_is_restricted_to_pipeline_positive_objects():
    data = _known_data()
    data["pipeline_exists"] = [1, 0, 0, 0]
    row = next(row for row in metric_rows(data, bootstrap=20) if row["metric"] == "PPV existence")
    assert row["estimate"] == pytest.approx(1.0)


def test_vending_precision_and_whole_object_uncertainty():
    data = _known_data()
    data.loc[data.unit_id.eq("a"), "is_vending"] = "yes"
    rows = {row["metric"]: row for row in metric_rows(data, bootstrap=10)}
    assert rows["confirmed vending"]["estimate"] == pytest.approx(1)
    assert rows["whole object correct"]["estimate"] == pytest.approx(1 / 3)
    assert rows["whole object correct"]["unclear_high"] > rows["whole object correct"]["unclear_low"]


def test_missing_audit_label_is_not_silently_dropped_by_join():
    sample = _known_data().drop(columns=["E", "K", "pipeline_exists", "pipeline_kind"])
    labels = pd.DataFrame({"unit_id": ["a"], "exists": ["yes"], "open": ["open"], "category_ok": ["yes"]})
    # add_reference remains a pure join; the CLI evaluator validates complete IDs.
    assert len(add_reference(sample, labels)) == 1


def test_evaluate_writes_known_synthetic_population_tables(tmp_path):
    sample = _known_data()[["unit_id", "delivery", "category", "stratum", "ver_exists", "ver_kind"]].copy()
    sample["city"] = "Новодвинск"
    sample["n_sources_max"] = [2, 1, 3, 0]
    sample["sources"] = ""
    sample_path = tmp_path / "sample_B.csv"
    sample.to_csv(sample_path, index=False)
    allocation_path = tmp_path / "allocation.tsv"
    pd.DataFrame({"delivery": ["d"], "stratum": ["S1"], "N_h": [8], "n_B_h": [4]}).to_csv(
        allocation_path, sep="\t", index=False
    )
    labels = pd.DataFrame(
        {
            "unit_id": ["a", "b", "c", "d"],
            "exists": ["yes", "yes", "no", "yes"],
            "open": ["open", "closed", "closed", "open"],
            "category_ok": ["yes", "no", "yes", "unclear"],
            "url_types": ["other"] * 4,
            "verified_url_types": [""] * 4,
            "is_vending": ["no", "no", "yes", "no"],
        }
    )
    labels_path = tmp_path / "auditor_labels_B.csv"
    labels.to_csv(labels_path, index=False)
    output = tmp_path / "out"
    result = evaluate(sample_path, allocation_path, labels_path, output, bootstrap=25)
    ppv = pd.read_csv(output / "metrics.csv").query("metric == 'PPV existence'").iloc[0]
    assert ppv.estimate == pytest.approx(0.5)
    assert len(pd.read_csv(output / "confusion_kind.csv")) == 18
    assert len(result) == 4
    assert "confirmed vending" in set(pd.read_csv(output / "metrics.csv").metric)


def test_human_kappa_reads_and_validates_completed_xlsx(tmp_path):
    from openpyxl import Workbook

    data = _known_data()
    path = tmp_path / "human.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS)
    for row in data.itertuples():
        sheet.append(
            [
                row.unit_id,
                "name",
                "address",
                "city",
                "claimed",
                0,
                0,
                "https://yandex.ru",
                "https://2gis.ru",
                "https://google.com",
                row.exists,
                row.open,
                row.category_ok,
                row.is_vending,
            ]
        )
    workbook.save(path)
    human_kappa(data, path, tmp_path)
    assert set(pd.read_csv(tmp_path / "human_kappa.csv").axis) == {"exists", "open", "category_ok", "E"}
    assert len(pd.read_csv(tmp_path / "human_confusion.csv")) > 0


def _two_strata(e_s2):
    """S1 with three resolved units and S2 (N = 10) whose labels are given."""
    n2 = len(e_s2)
    return pd.DataFrame(
        {
            "unit_id": [f"u{i}" for i in range(3 + n2)],
            "delivery": ["d"] * (3 + n2),
            "stratum": ["S1"] * 3 + ["S2"] * n2,
            "population_N": [30] * 3 + [10] * n2,
            "y": [1.0, 0.0, 1.0, *e_s2],
            "one": 1.0,
        }
    )


def test_singleton_resolved_stratum_is_pooled_for_variance_only():
    # S2 has five sampled units, four unresolved: one resolved label remains.
    data = _two_strata([1.0, np.nan, np.nan, np.nan, np.nan])
    result = weighted_ratio(data, "y", "one", bootstrap=50)
    # Point weights stay N_h / n_h on resolved units: (30 * 2/3 + 10 * 1) / 40.
    assert result["estimate"] == pytest.approx((20 + 10) / 40)
    assert np.isfinite(result["low"]) and result["low"] < result["estimate"] < result["high"]


def test_variance_groups_is_shared_with_the_grid_and_follows_its_rule():
    from infracheck.audit import grid

    assert grid.variance_groups is variance_groups
    alloc = pd.DataFrame(
        {"delivery": ["d"] * 4, "stratum": ["S1", "S2", "S3", "S4"], "N_h": [30, 10, 20, 40], "n": [3, 1, 1, 1]}
    )
    data = pd.DataFrame({"delivery": ["d"] * 6, "stratum": ["S1", "S1", "S1", "S2", "S3", "S4"]})
    pooled, groups = variance_groups(data, alloc, "n")
    # S2 -> S1 (grid rule); S4 -> S3, S3 -> S1 (extension): one group of every stratum.
    assert set(pooled.variance_stratum) == {"S1"}
    assert groups.n.tolist() == [6]
    lonely = pd.DataFrame({"delivery": ["e"], "stratum": ["S4"], "N_h": [27], "n": [1]})
    _, alone = variance_groups(lonely.drop(columns=["N_h", "n"]), lonely, "n")
    assert alone.n.tolist() == [1]  # No partner in the delivery: left to the caller.


def test_unpooled_variance_matches_protocol_formula():
    data = _two_strata([1.0, 0.0, 0.0, 1.0, 1.0])
    result = weighted_ratio(data, "y", "one", bootstrap=0)
    w = data.population_N / data.groupby("stratum").unit_id.transform("size")
    theta = (w * data.y).sum() / w.sum()
    z = (data.y - theta) / w.sum()
    variance = sum(
        g.population_N.iloc[0] ** 2 * (1 - len(g) / g.population_N.iloc[0]) * z[g.index].var(ddof=1) / len(g)
        for _, g in data.groupby("stratum")
    )
    from statistics import NormalDist

    zq = NormalDist().inv_cdf(0.975)
    n_eff = theta * (1 - theta) / variance
    centre = (theta + zq**2 / (2 * n_eff)) / (1 + zq**2 / n_eff)
    assert (result["low"] + result["high"]) / 2 == pytest.approx(centre)


def test_specificity_bounds_are_ordered():
    data = _known_data()
    data.loc[data.unit_id.isin(["a", "c"]), "E"] = np.nan
    rows = {row["metric"]: row for row in metric_rows(data, bootstrap=0)}
    for metric in ("sensitivity existence", "specificity existence", "PPV existence", "NPV existence"):
        assert rows[metric]["unclear_low"] <= rows[metric]["unclear_high"], metric


def test_rests_on_yandex_card_only_when_every_verified_card_is_yandex():
    from infracheck.audit.evaluate import rests_on_yandex_card

    row = {
        "label_strength": "live_card",
        "urls": "https://yandex.ru/maps/org/tortik/1/;https://arkh.bebeshka.info/tortik/",
        "url_outcomes": "verified;verified",
    }
    assert rests_on_yandex_card(row, "Тортик")
    assert not rests_on_yandex_card({**row, "label_strength": "dated"}, "Тортик")
    with_vk = {**row, "urls": row["urls"] + ";https://vk.com/tortik", "url_outcomes": "verified;verified;verified"}
    assert not rests_on_yandex_card(with_vk, "Тортик")
    assert not rests_on_yandex_card({**row, "url_outcomes": "unreachable;verified"}, "Тортик")
