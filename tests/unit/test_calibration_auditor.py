import json
from unittest.mock import Mock

import pytest

from infracheck.audit.auditor import run_batch


def test_batch_parses_json_and_usage(monkeypatch):
    def run(cmd, **kwargs):
        output = cmd[cmd.index("-o") + 1]
        with open(output, "w") as handle:
            json.dump(
                {
                    "objects": [
                        dict(
                            unit_id="x",
                            exists="yes",
                            open="open",
                            category_ok="yes",
                            is_vending="no",
                            quote="q",
                            urls=["https://example.org"],
                            evidence_date="2026",
                            notes="",
                        )
                    ]
                },
                handle,
            )
        return Mock(stdout='{"type":"turn.completed","usage":{"input_tokens":4}}\n')

    monkeypatch.setattr("infracheck.audit.auditor.subprocess.run", run)
    labels, usage = run_batch([{"unit_id": "x"}], "template", "model", "medium")
    assert labels[0]["unit_id"] == "x"
    assert usage["input_tokens"] == 4


def test_batch_rejects_incomplete_response(monkeypatch):
    def run(cmd, **kwargs):
        with open(cmd[cmd.index("-o") + 1], "w") as handle:
            json.dump({"objects": []}, handle)
        return Mock(stdout="")

    monkeypatch.setattr("infracheck.audit.auditor.subprocess.run", run)
    with pytest.raises(ValueError):
        run_batch([{"unit_id": "x"}], "template", "model", "medium")


def test_audit_resume_retry_and_escalation(tmp_path, monkeypatch):
    import pandas as pd
    import yaml

    from infracheck.audit.auditor import audit

    output, cache = tmp_path / "out", tmp_path / "cache"
    output.mkdir()
    pd.DataFrame([{"unit_id": "x", "name": "Cafe"}, {"unit_id": "y", "name": "Shop"}]).to_csv(
        output / "auditor_input_B.csv", index=False
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("---\nprompt_version: 1\n---\nCheck")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "paths": {"output": str(output), "cache": str(cache), "prompt": str(prompt)},
                "models": {"default": "base", "effort": "medium", "escalation": "strong", "escalation_effort": "high"},
            }
        )
    )
    calls = []

    def batch(rows, template, model, effort):
        calls.append((model, [row["unit_id"] for row in rows]))
        if len(calls) == 1:
            raise ValueError("bad response")
        return (
            [
                dict(
                    unit_id=row["unit_id"],
                    exists="unclear" if model == "base" else "yes",
                    open="open",
                    category_ok="yes",
                    is_vending="no",
                    quote="q",
                    urls=["https://2gis.ru/a"],
                    evidence_date="2026",
                    notes="",
                )
                for row in rows
            ],
            {"input_tokens": 2},
        )

    monkeypatch.setattr("infracheck.audit.auditor.run_batch", batch)
    monkeypatch.setattr("infracheck.audit.verify.URLVerifier.verify", lambda *args: True)
    audit("B", batch=2, config_path=config)
    audit("B", config_path=config)
    assert len(calls) == 2
    audit("B", escalate=True, config_path=config)
    assert calls[-1] == ("strong", ["x", "y"])
    audit("B", escalate=True, config_path=config)
    assert len(calls) == 3
    exported = pd.read_csv(output / "auditor_labels_B.csv")
    assert set(exported.exists) == {"yes"}
    assert set(exported.initial_exists) == {"unclear"}
    assert set(exported.url_types) == {"2gis"}
    lines = [json.loads(line) for line in (cache / "audit_B.jsonl").read_text().splitlines()]
    assert len(lines) == 4 and all(line["token_usage"]["input_tokens"] == 2 for line in lines)


def test_failed_batch_never_cached(tmp_path, monkeypatch):
    import pandas as pd
    import yaml

    from infracheck.audit.auditor import audit

    out = tmp_path / "out"
    out.mkdir()
    pd.DataFrame([{"unit_id": "x", "name": "Name"}]).to_csv(out / "auditor_input_A.csv", index=False)
    prompt = tmp_path / "prompt"
    prompt.write_text("---\nprompt_version: 1\n---\n")
    config = tmp_path / "config"
    config.write_text(
        yaml.safe_dump(
            {
                "paths": {"output": str(out), "cache": str(tmp_path / "cache"), "prompt": str(prompt)},
                "models": {"default": "m", "effort": "medium"},
            }
        )
    )
    calls = []

    def fail(*args):
        calls.append(1)
        raise ValueError("malformed")

    monkeypatch.setattr("infracheck.audit.auditor.run_batch", fail)
    with pytest.raises(RuntimeError, match="failed"):
        audit("A", config_path=config)
    assert len(calls) == 2
    assert (tmp_path / "cache" / "audit_A.jsonl").read_text() == ""
