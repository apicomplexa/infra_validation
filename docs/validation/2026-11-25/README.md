# Оценка качества на базе 2026-11-25: сводные таблицы

Числа, которые цитируют `docs/methodology_finalest.md` (§8, §9), README и
документы руководителю (`Методика/*.docx`). Только агрегаты и вердикты по
идентификаторам: ни названий, ни адресов, ни текста карточек. Репозиторий
публичный, а пакеты судей и метки аудитора содержат данные Яндекса и 2ГИС —
они в git не идут (CLAUDE.md, «Data in … must not be published»).

| Папка | Что | Откуда |
|---|---|---|
| `sample_B/base/` | метрики выборки B для «В базу» единой базы | `pixi run calibration-current` → `results/finalest/calibration/base/` |
| `sample_B/current/` | то же для «Подтверждено (объект)» | `results/finalest/calibration/current/` |
| `sample_B/verdicts_now.csv` | вердикты каждого объекта B, на которых считалось | `results/finalest/calibration/verdicts_now.csv` |
| `internal_validity/` | слепые судьи: метрики, согласие, страты, манифест выборки, список судей | `results/finalest/internal_validity_v2/` и его `eval/` |

`label_flags.csv` — метка аудитора по объекту (существует / работает / вид) и
флаги зависимости, без ссылок и текста.

## Где сырьё

Локально, вне git, рядом с кэшем Яндекса и с тем же режимом (делать копию,
не коммитить): `resources/validation/2026-11-25/`

- `internal_validity_v2/` — пакеты, ответы обоих судей (`judgments_*.jsonl`),
  `decisions.csv`, `population.csv`, `eval/labels.csv`, `eval/examples.csv`;
- `calibration/` — `sample_B.csv`, `allocation.tsv`, `auditor_input_B.csv`,
  `auditor_labels_B.csv` (ссылки и обоснования аудитора), `base_snapshot.xlsx`,
  полные папки `base/` и `current/`.

Рабочие копии остаются в `results/finalest/`: оттуда их читают
`build_deliverable.py` и `report_docx.py`. Если `results/` потерян, скопируйте
`resources/validation/2026-11-25/internal_validity_v2` в
`results/finalest/internal_validity_v2`, а содержимое `calibration/` — в
`results/finalest/calibration/`.
