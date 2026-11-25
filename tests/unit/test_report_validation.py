"""The supervisor's report and the metrics it quotes: wording helpers and staleness guards."""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "workflow" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


rd = load("report_docx")
cc = load("calibration_current")
bd = load("build_deliverable")


@pytest.mark.parametrize(
    ("k", "text"),
    [
        (1, "1 объект"),
        (2, "2 объекта"),
        (5, "5 объектов"),
        (11, "11 объектов"),
        (12, "12 объектов"),
        (21, "21 объект"),
        (671, "671 объект"),
        (672, "672 объекта"),
        (1062, "1 062 объекта"),
    ],
)
def test_object_counts_agree_in_russian(k, text):
    assert rd.objs(k) == text


def test_adjective_follows_the_number():
    assert rd.objs(671, "подтверждённых") == "671 подтверждённый объект"
    assert rd.objs(672, "подтверждённых") == "672 подтверждённых объекта"
    assert rd.objs(508, "спорных") == "508 спорных объектов"


def test_model_name_is_read_from_the_judge_label():
    assert rd.model("Судья 1 (gpt-6.2-sol)") == "gpt-6.2-sol"


def base_rows(**over):
    row = {"ID": "d:1", "Подтверждено": 1, "Вид?": "да", "В базу": 1, "Почему не в базе": None}
    row.update(over)
    return pd.DataFrame([row])


def test_a_sampled_unit_missing_from_the_base_is_an_error():
    sample = pd.DataFrame(
        [
            {
                "unit_id": "d:2",
                "category": "catering",
                "city": "А",
                "stratum": "S1",
                "ver_exists": "conf",
                "ver_kind": "да",
            }
        ]
    )
    with pytest.raises(RuntimeError, match="not rows of the base"):
        cc.verdicts(sample, base_rows())


def test_verdicts_take_the_base_side_only():
    sample = pd.DataFrame(
        [
            {
                "unit_id": "d:1",
                "category": "catering",
                "city": "А",
                "stratum": "S1",
                "ver_exists": "not",
                "ver_kind": "нет данных",
            }
        ]
    )
    v = cc.verdicts(sample, base_rows(**{"В базу": 0, "Вид?": "нет"}))
    assert v.loc[0, ["confirmed_now", "in_base", "kind_now"]].tolist() == [1, 0, "нет"]
    assert v.loc[0, "ver_exists_sampled"] == "not"


def test_deliverable_refuses_metrics_of_another_base(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "CAL", tmp_path)
    pd.DataFrame([{"unit_id": "d:1", "in_base": 1, "confirmed_now": 1}]).to_csv(
        tmp_path / "verdicts_now.csv", index=False, encoding="utf-8-sig"
    )
    bd.check_metrics_fresh(base_rows())  # the same base: fine
    with pytest.raises(SystemExit, match="stale"):
        bd.check_metrics_fresh(base_rows(**{"В базу": 0}))
