#!/usr/bin/env bash
# Blind LLM judges for an internal-validity sample (stage 1.5 between
# internal_validity_sample.py and internal_validity_eval.py), through Codex CLI.
#
# Each call sees only judge_prompt.md and one packets file, copied into an empty
# folder, in a read-only sandbox: no decisions.csv, no pipeline verdict, no
# network search (the prompt forbids it). Two models, four packet files each,
# run in parallel. Answers are checked to cover every decision exactly once and
# written as judgments_<tag>.jsonl beside the packets, with judges.json naming
# them for the evaluator.
#
# Usage: workflow/scripts/internal_validity_judge.sh results/finalest/internal_validity_v2 [model1 model2]
# Network: yes (the models). Not part of any Snakemake target.
set -euo pipefail

IV=$(realpath "$1")
M1=${2:-gpt-6.2-sol}
M2=${3:-gpt-6-luna}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

judge() { # model tag k
  local w="$WORK/$2_$3"
  mkdir -p "$w"
  cp "$IV/judge_prompt.md" "$w/judge_prompt.md"
  cp "$IV/packets_$3.jsonl" "$w/packets.jsonl"
  local n
  n=$(wc -l <"$w/packets.jsonl")
  codex exec --skip-git-repo-check --ephemeral -s read-only -C "$w" -m "$1" -c model_reasoning_effort=high \
    -o "$w/answer.txt" \
    "Ты — судья. Прочитай judge_prompt.md в текущей папке и следуй ему. Пакеты — в файле packets.jsonl ($n строк). Других файлов не открывай и в сети ничего не ищи. Ответ — ровно $n строк JSON, по одной на пакет, без другого текста и без обёртки в \`\`\`." \
    >"$w/log.txt" 2>&1
}

for k in 1 2 3 4; do
  judge "$M1" j1 "$k" &
  judge "$M2" j2 "$k" &
done
wait

python3 - "$IV" "$WORK" "$M1" "$M2" <<'EOF'
import json, sys, pathlib
iv, work, m1, m2 = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
names = {"j1": m1, "j2": m2}
for tag, model in names.items():
    rows = []
    for k in (1, 2, 3, 4):
        ids = [json.loads(l)["decision_id"] for l in open(iv / f"packets_{k}.jsonl", encoding="utf-8")]
        text = (work / f"{tag}_{k}" / "answer.txt").read_text(encoding="utf-8")
        got = [json.loads(l) for l in text.splitlines() if l.strip().startswith("{")]
        seen = [g["decision_id"] for g in got]
        if sorted(seen) != sorted(ids):
            raise SystemExit(f"{model}, packets_{k}: answers do not cover each decision exactly once")
        rows += [{f: g[f] for f in ("decision_id", "same_org", "open", "reason")} for g in got]
    with open(iv / f"judgments_{model}.jsonl", "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(model, len(rows))
spec = {"judge1": [f"Судья 1 ({m1})", [f"judgments_{m1}.jsonl"]],
        "judge2": [f"Судья 2 ({m2})", [f"judgments_{m2}.jsonl"]],
        "_runner": "workflow/scripts/internal_validity_judge.sh: Codex CLI, codex exec -s read-only, "
                   "reasoning effort high; each judge saw judge_prompt.md and one packet file only"}
(iv / "judges.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
EOF
