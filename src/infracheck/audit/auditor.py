"""Run resumable blind audits through Codex and export verified labels."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml

from infracheck.audit.verify import URLVerifier, verify_label

AXES = {
    "exists": ["yes", "no", "unclear"],
    "open": ["open", "closed", "unclear"],
    "category_ok": ["yes", "no", "unclear"],
    "is_vending": ["yes", "no", "unclear"],
}
FIELDS = [
    "unit_id",
    "exists",
    "open",
    "category_ok",
    "is_vending",
    "quote",
    "urls",
    "url_types",
    "evidence_date",
    "audit_date",
    "auditor_model",
    "escalated",
    "notes",
]
PROPERTIES = {
    **{k: {"type": "string", "enum": v} for k, v in AXES.items()},
    **{k: {"type": "string"} for k in ["unit_id", "quote", "evidence_date", "notes"]},
    "urls": {"type": "array", "items": {"type": "string"}},
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["objects"],
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(PROPERTIES),
                "properties": PROPERTIES,
            },
        }
    },
}


def load_cache(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_batch(rows, template, model, effort, timeout=900):
    prompt = template + "\n\nОбъекты:\n" + json.dumps(rows, ensure_ascii=False)
    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        schema = tmp / "schema.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        output = tmp / "answer.json"
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-c",
            'web_search="live"',
            "-m",
            model,
            "-c",
            f"model_reasoning_effort={effort}",
            "-s",
            "read-only",
            "--output-schema",
            str(schema),
            "-o",
            str(output),
            prompt,
        ]
        result = subprocess.run(command, cwd=tmp, capture_output=True, text=True, timeout=timeout, check=True)
        usage = None
        for line in result.stdout.splitlines():
            event = json.loads(line)
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
        answer = json.loads(output.read_text(encoding="utf-8"))["objects"]
        expected = {r["unit_id"] for r in rows}
        if len(answer) != len(expected) or {r.get("unit_id") for r in answer} != expected:
            raise ValueError("Response does not match batch")
        for label in answer:
            if set(label) != set(PROPERTIES) or any(label[k] not in vals for k, vals in AXES.items()):
                raise ValueError("Invalid response")
            if not isinstance(label["urls"], list) or any(not isinstance(x, str) for x in label["urls"]):
                raise ValueError("Invalid URLs")
            if any(not isinstance(label[k], str) for k in ("unit_id", "quote", "evidence_date", "notes")):
                raise ValueError("Invalid response fields")
        return answer, usage


def audit(
    sample,
    limit=None,
    batch=8,
    model=None,
    effort=None,
    dry_run=False,
    escalate=False,
    config_path="config/calibration.yaml",
):
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    paths = cfg["paths"]
    models = cfg["models"]
    model = model or models["escalation" if escalate else "default"]
    effort = effort or models["escalation_effort" if escalate else "effort"]
    if escalate:
        batch = 3
    if batch < 1 or (limit is not None and limit < 0):
        raise ValueError("Invalid batch size or limit")
    inputs = pd.read_csv(Path(paths["output"]) / f"auditor_input_{sample}.csv").fillna("")
    cache = Path(paths["cache"]) / f"audit_{sample}.jsonl"
    existing = load_cache(cache)
    prompt = Path(paths["prompt"]).read_text(encoding="utf-8")
    front = yaml.safe_load(prompt.split("---", 2)[1])
    version = str(front["prompt_version"])
    latest = {x["unit_id"]: x for x in existing}
    if escalate:
        verified_path = Path(paths["output"]) / f"auditor_labels_{sample}.csv"
        if not verified_path.exists():
            raise FileNotFoundError("Verified labels are required before escalation")
        verified = pd.read_csv(verified_path).fillna("")
        latest = {row["unit_id"]: row for row in verified.to_dict("records")}
    cached = {(x["unit_id"], str(x["prompt_version"]), x["auditor_model"]) for x in existing}
    candidates = []
    for row in inputs.to_dict("records"):
        unit = row["unit_id"]
        if (unit, version, model) in cached:
            continue
        if escalate and (unit not in latest or not any(latest[unit].get(k) == "unclear" for k in AXES)):
            continue
        candidates.append(row)
    if limit is not None:
        candidates = candidates[:limit]
    if dry_run:
        print(f"Объектов: {len(candidates)}; пакетов: {(len(candidates) + batch - 1) // batch}")
        return
    cache.parent.mkdir(parents=True, exist_ok=True)
    failures = []
    for start in range(0, len(candidates), batch):
        rows = candidates[start : start + batch]
        for attempt in range(2):
            try:
                labels, usage = run_batch(rows, prompt, model, effort)
                break
            except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
                if attempt == 1:
                    failures.append(str(exc))
                    labels = []
        batch_id = uuid.uuid4().hex
        with cache.open("a", encoding="utf-8") as handle:
            for label in labels:
                label.update(
                    prompt_version=version,
                    auditor_model=model,
                    escalated=escalate,
                    audit_date=datetime.now(UTC).isoformat(),
                    raw_batch_id=batch_id,
                    token_usage=usage,
                )
                handle.write(json.dumps(label, ensure_ascii=False) + "\n")
    export(sample, inputs, cache, Path(paths["output"]), Path(paths["cache"]) / "urls")
    if failures:
        raise RuntimeError(f"{len(failures)} batch(es) failed: {failures}")


def export(sample, inputs, cache, output, url_cache):
    names = inputs.set_index("unit_id")["name"].to_dict()
    latest = {row["unit_id"]: row for row in load_cache(cache) if row["unit_id"] in names}
    verifier = URLVerifier(url_cache)
    with (output / f"auditor_labels_{sample}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for unit in sorted(latest):
            label = verify_label(latest[unit], names[unit], verifier)
            for key in ("urls", "url_types"):
                label[key] = ";".join(label.get(key) or [])
            writer.writerow(label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", required=True, choices=["A", "B"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--escalate", action="store_true")
    audit(**vars(parser.parse_args()))


if __name__ == "__main__":
    main()
