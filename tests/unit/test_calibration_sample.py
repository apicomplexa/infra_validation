import pandas as pd
import pytest

from infracheck.audit.sample import allocate, build_frame


def records():
    base = dict(
        delivery="d",
        city="Архангельск",
        category="catering",
        name="Имя",
        address="Адрес",
        lat=1,
        lon=2,
        types="Кафе",
        n_sources=1,
        sources="OSM",
        ya_status="present",
        needs_yandex=True,
        food="да",
        src_osm=True,
    )
    return pd.DataFrame(
        [
            dict(base, object_id="d:0", merged=0, confirmed=0, confirmed_object=1),
            dict(base, object_id="d:1", merged=0, confirmed=1, confirmed_object=1),
            dict(base, object_id="d:2", merged=2, confirmed=0, confirmed_object=0),
        ]
    )


def test_pooled_verdict_and_stable_ranking():
    frame = build_frame(records(), "seed")
    assert len(frame) == 2
    first = frame.set_index("merged").loc[0]
    assert first.ver_exists == "conf" and first.mixed and first.stratum == "S1"
    pd.testing.assert_frame_equal(frame, build_frame(records().iloc[::-1], "seed"))


def test_require_pooled_verdict_and_finished_yandex():
    with pytest.raises(ValueError, match="confirmed_object"):
        build_frame(records().drop(columns="confirmed_object"), "seed")
    data = records()
    data.loc[0, "ya_status"] = ""
    with pytest.raises(ValueError, match="Yandex"):
        build_frame(data, "seed")


def test_allocation_respects_caps_and_minima():
    result = allocate(
        {"S1": 30, "S2": 3, "S3": 10}, 15, {"S1": 30**0.5, "S2": 3**0.5, "S3": 10**0.5}, {"S1": 5, "S2": 3, "S3": 5}
    )
    assert sum(result.values()) == 15
    assert result["S2"] == 3
    assert all(result[x] >= 5 for x in ("S1", "S3"))


def test_sample_end_to_end_invariants(tmp_path, monkeypatch):
    from unittest.mock import Mock

    import yaml

    from infracheck.audit.sample import BLIND, sample

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("infracheck.audit.sample.subprocess.run", lambda *a, **kw: Mock(stdout="commit\n"))
    objects = tmp_path / "objects"
    objects.mkdir()
    for category in ("catering", "alcohol", "tobacco"):
        data = pd.concat([records()] * 4, ignore_index=True)
        data["category"] = category
        data["delivery"] = category
        data["object_id"] = [f"{category}:{i}" for i in range(len(data))]
        data["merged"] = [i // 2 * 2 for i in range(len(data))]
        data["confirmed_object"] = [i // 2 % 2 for i in range(len(data))]
        data["alcohol"] = data["tobacco"] = "да"
        data.to_csv(objects / f"{category}_objects.csv", sep=";", index=False)
    pd.DataFrame({"delivery": ["catering", "alcohol", "tobacco"]}).to_csv("deliveries.tsv", sep="\t", index=False)
    (tmp_path / "pipeline.yaml").write_text("categories: {}\n")
    cfg = {
        "seed": "fixed",
        "n_A": {"Архангельск": 2},
        "n_B": {"Архангельск": 2},
        "min_per_stratum": 1,
        "paths": {
            "objects_dir": "objects",
            "deliveries": "deliveries.tsv",
            "pipeline_config": "pipeline.yaml",
            "output": "out",
        },
    }
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True))
    sample("config.yaml")
    out = tmp_path / "out"
    snapshot = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FileExistsError, match="Manifest"):
        sample("config.yaml")
    frame = pd.read_csv(out / "frame.csv")
    a = pd.read_csv(out / "sample_A.csv")
    b = pd.read_csv(out / "sample_B.csv")
    assert not set(a.unit_id) & set(b.unit_id)
    assert not frame.duplicated(["delivery", "merged"]).any()
    assert a.groupby("delivery").size().eq(2).all()
    assert b.groupby("delivery").size().eq(2).all()
    allocation = pd.read_csv(out / "allocation.tsv", sep="\t")
    assert allocation.groupby("delivery")[["n_A_h", "n_B_h"]].sum().eq(2).all().all()
    for label in ("A", "B"):
        blind = pd.read_csv(out / f"auditor_input_{label}.csv")
        assert list(blind.columns) == BLIND
        assert blind.unit_id.tolist() == sorted(blind.unit_id)
    (out / "manifest.json").unlink()
    sample("config.yaml")
    assert snapshot == {p.name: p.read_bytes() for p in out.iterdir()}
