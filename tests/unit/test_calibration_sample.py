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
