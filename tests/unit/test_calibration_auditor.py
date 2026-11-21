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
