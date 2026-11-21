import copy
from pathlib import Path

import pandas as pd
import yaml

from infracheck.audit import grid_runner
from infracheck.core import evidence


def test_run_point_rejudges_but_keeps_sample_unit_frozen(tmp_path, monkeypatch):
    config = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))
    delivery = "fixture_catering"
    object_ids = [f"{delivery}:0", f"{delivery}:1"]
    objects = pd.DataFrame(
        {
            "delivery": [delivery, delivery], "city": ["Тест", "Тест"],
            "name": ["Кафе", "Кафе"], "address": ["1", "1"],
            "lat": [1.0, 1.0], "lon": [1.0, 1.0], "site_key": ["a", "b"],
            "category": ["catering", "catering"], "generic": [False, False],
            "n_rows": [1, 1], "entity": [0, 1], "types": ["Кафе", "Кафе"],
        }, index=pd.Index(object_ids, name="object_id")
    )
    providers = evidence.make([{
        "object_id": object_ids[0], "check": "providers", "source": "osm",
        "identity": "strong", "record_state": "current", "label": "OSM",
    }])
    empty = evidence.empty()
    root = tmp_path
    delivery_dir = root / "data" / "temp" / delivery
    (delivery_dir / "evidence").mkdir(parents=True)
    object_path = delivery_dir / "objects.parquet"
    object_path.touch()
    for check in config["categories"]["catering"]["checks"]:
        (delivery_dir / "evidence" / f"{check}.parquet").touch()

    def read_parquet(path):
        path = str(path)
        if path.endswith("objects.parquet"):
            return objects.copy()
        if path.endswith("evidence/providers.parquet"):
            return providers.copy()
        return empty.copy()

    calls = []

    def run(check, obj, policy, resource=None):
        calls.append((check, policy.name_min))
        if policy.name_min <= 65:
            return evidence.make([{
                "object_id": object_ids[1], "check": "yandex", "source": "yandex",
                "identity": "strong", "record_state": "current", "label": "Яндекс",
            }])
        return evidence.empty()

    monkeypatch.setattr(grid_runner.pipeline, "read_parquet", read_parquet)
    monkeypatch.setattr(grid_runner.pipeline, "decide", lambda *args: None)
    monkeypatch.setattr(grid_runner.registry, "run", run)
    units = pd.DataFrame([{
        "unit_id": f"{delivery}:fixed", "delivery": delivery,
        "category": "catering", "object_ids": ";".join(object_ids),
    }])

    baseline = grid_runner.run_point(config, "catering", config["categories"]["catering"], units, root)
    candidate = copy.deepcopy(config["categories"]["catering"])
    candidate["policy"]["yandex"]["name_min"] = 65.0
    changed = grid_runner.run_point(config, "catering", candidate, units, root)

    assert baseline.confirmed_object.tolist() == [0]
    assert changed.confirmed_object.tolist() == [1]
    assert calls == [("yandex", 65.0)]
    assert changed.unit_id.tolist() == [f"{delivery}:fixed"]
