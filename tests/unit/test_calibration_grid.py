import pandas as pd
import pytest
import yaml

from infracheck.audit.grid import grid_points, override_category, read_labels, scores, select_point, variance_groups


def test_grid_overrides_keep_provider_and_limit_dimensions():
    with open("config/config.yaml") as f:
        category = yaml.safe_load(f)["categories"]["catering"]
    points = grid_points(category)
    assert len(points) == 46
    altered = override_category(category, points[-1])
    assert altered["policy"]["providers"] == category["policy"]["providers"]
    assert altered["policy"]["overture"]["name_min"] == altered["policy"]["foursquare"]["name_min"]
    for p in points:
        assert sum(a != b for a, b in zip(p, points[0], strict=True)) <= 3


def test_partial_labels_and_collapsed_strata(tmp_path):
    sample = pd.DataFrame({"unit_id": ["a", "b", "c"]})
    path = tmp_path / "labels.csv"
    assert read_labels(path, sample).empty
    pd.DataFrame([{"unit_id": "a", "exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no"}]).to_csv(
        path, index=False
    )
    assert len(read_labels(path, sample)) == 1
    alloc = pd.DataFrame({"delivery": ["d", "d"], "stratum": ["S1", "S2"], "N_h": [10, 4], "n_A_h": [2, 1]})
    data = pd.DataFrame(
        {
            "unit_id": ["a", "b", "c"],
            "delivery": ["d"] * 3,
            "stratum": ["S1", "S1", "S2"],
            "weight": [5.0, 5.0, 4.0],
            "E": [1.0, 0.0, 1.0],
            "K": [1.0, 0.0, 1.0],
            "identified": [True, True, True],
            "pipeline_exists": [1.0, 1.0, 0.0],
            "pipeline_kind": [1.0, 0.0, 1.0],
        }
    )
    collapsed, _ = variance_groups(data, alloc)
    assert collapsed.variance_stratum.tolist() == ["S1"] * 3
    result = scores(data, alloc, bootstrap=20)
    assert result["accuracy"] == pytest.approx(5 / 14)
    assert result["recall"] == pytest.approx(5 / 9)
    assert result["accuracy_se"] >= 0
    assert scores(data.iloc[:2], alloc, bootstrap=20)["accuracy"] == pytest.approx(0.5)


def test_selection_stop_tie_and_stability():
    base = {
        "point_id": "current",
        "balanced_accuracy": 0.7,
        "balanced_accuracy_se": 0.02,
        "kind_coverage": 0.8,
        "distance": 0,
        "stable": False,
    }
    candidate = {**base, "point_id": "new", "balanced_accuracy": 0.71, "distance": 1, "stable": True}
    assert select_point([base, candidate], "current")[0] == base
    candidate.update(balanced_accuracy=0.74, stable=False)
    assert select_point([base, candidate], "current")[0] == base
    candidate["stable"] = True
    assert select_point([base, candidate], "current")[0] == candidate
    candidate.update(balanced_accuracy=0.72, balanced_accuracy_se=0.03)
    assert select_point([base, candidate], "current")[0] == base


def test_run_with_mock_checks_and_partial_labels(tmp_path, monkeypatch):
    from pathlib import Path

    from infracheck.audit import grid

    (tmp_path / "config").mkdir()
    (tmp_path / "config/config.yaml").write_text(Path("config/config.yaml").read_text())
    cal = tmp_path / "results/finalest/calibration"
    cal.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "unit_id": "d:1",
                "delivery": "d",
                "category": "catering",
                "city": "city",
                "stratum": "S1",
                "object_ids": "d:1;d:2",
                "ver_exists": "conf",
                "ver_kind": "да",
            },
            {
                "unit_id": "d:3",
                "delivery": "d",
                "category": "catering",
                "city": "city",
                "stratum": "S1",
                "object_ids": "d:3",
                "ver_exists": "not",
                "ver_kind": "нет",
            },
        ]
    ).to_csv(cal / "sample_A.csv", index=False)
    pd.DataFrame([{"delivery": "d", "stratum": "S1", "N_h": 10, "n_A_h": 2}]).to_csv(
        cal / "allocation.tsv", sep="\t", index=False
    )
    pd.DataFrame(
        [{"unit_id": "d:1", "exists": "yes", "open": "open", "category_ok": "yes", "is_vending": "no"}]
    ).to_csv(cal / "auditor_labels_A.csv", index=False)

    def mock_run(config, name, category, sample, root):
        assert category["policy"]["providers"] == config["categories"][name]["policy"]["providers"]
        return pd.DataFrame({"unit_id": sample.unit_id, "confirmed_object": [1, 0], "ver_kind": ["да", "нет"]})

    monkeypatch.setattr(grid, "run_point", mock_run)
    grid.run(tmp_path, bootstrap=3)
    text = (cal / "grid/grid_catering.md").read_text()
    assert "размечено 1 из 2" in text
    metrics = pd.read_csv(cal / "grid/metrics_catering.csv")
    assert len(metrics) == 46
    assert metrics.loc[0, "precision"] == 1
    assert pd.isna(metrics.loc[0, "precision_low"])
    assert yaml.safe_load((cal / "grid/recommended.yaml").read_text())["catering"]["decision"] == "keep current"
