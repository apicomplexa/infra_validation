# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

A validation pipeline for urban-infrastructure objects delivered by a
contractor (Arkhangelsk, Severodvinsk, Novodvinsk): every object is checked
against independent sources and the delivered workbook gets «Подтверждено»,
«Вид?» and «Основания». Ported one-to-one from the archive repository
`../infrastructure_2026` (tag `archive-2026-10-30`), whose CLAUDE.md and docs
hold the history of every rule. Read `docs/ARCHITECTURE.md` before changing
anything structural.

## Environment & commands

pixi only (Linux x86-64; no Windows). One environment serves Snakemake, the
rule scripts, `src/infracheck`, `packages/licmatch` (both editable installs),
Quarto and the dev tools; rules run in it directly (`workflow/envs/default.yaml`
is exported for documentation, never deployed).

- `pixi run workflow` — whole pipeline, offline; `pixi run dry-run`, `pixi run dag`
- `pixi run test` — unit + golden regression; `pixi run licmatch-test` — package only
- `pixi run lint` / `pixi run fmt` — ruff + snakefmt + snakemake --lint
- `pixi run yandex-fetch` — the one network step (appends to the Yandex cache);
  never part of `all`. Do not run it without the user's go-ahead.
- `pixi run docs` — render `docs/architecture.qmd` to `results/docs/`

## Layers and their rules

- `core/` — contracts (`ObjectTable`, `Evidence`), name and geo helpers.
- `inputs/` — a delivery file → ObjectTable + RowMap. A row is not an object.
- `checks/` — one source each: `Policy` dataclass, `run(...) -> Evidence`,
  `wide(ev, index)` rebuilding the archive's columns. **Category-agnostic**:
  no category names, no verdicts; everything that changes a result is in its
  Policy, built from `config.yaml`.
- `packages/licmatch` — the register matcher, a pure five-stage `match()`;
  catering and the v4 alcohol/tobacco join are two Policies of it. It knows
  nothing of EGAIS or of Russian «Основания» texts — `checks/egais.py` adapts.
- `categories/base.py` — the generic rule over Evidence; a category is a config
  block. Counting: `identity = strong` and `record_state = current`, once per
  independence group. Deviations needed for parity are explicit in config.
- `deliver/xlsx.py` — the workbook copy, front columns, appended evidence, legend.

## Invariants

- **Behaviour-preserving first.** Golden outputs of the archive are in
  `resources/golden/` (not in git); the regression tests must stay exact. A
  change that moves results is its own commit with a `docs/changes/<date>-*.md`
  what-changed table, and the regression asserts the expected differences
  explicitly — never loosen a comparison to make a test pass.
- **Raw inputs are read-only**: `data/raw/`, `resources/egais|overture|foursquare`.
  `resources/cache/yandex/*.jsonl` is valuable state, appended only by
  `yandex-fetch`; it is keyed by the query (`search_url`), not by the object.
- **Never sum evidence kinds into one headline**: tobacco has no register, so an
  alcohol licence is a `probable` hint after « || », never a confirmation.
- **Alcohol**: an EGAIS licence counts only when the name agrees (direct,
  alias, or a learned brand pair passing leave-one-out); address-only or a
  generic fascia is weak.
- The EGAIS v4 join is exclusive across both risk layers: deliveries sharing a
  `match_group` in `deliveries.tsv` are matched in one run.
- Reports and user-facing texts are Russian only; code and comments English,
  with docstrings that explain why (match the surrounding density).

## Data conventions

- Delivery XLSX: sheet `data` + `original_data` + `quality`; `quality` is not
  row-aligned with `data` and joins through `original_data` on five keys
  (`inputs/final_xlsx.py` checks it is 1:1).
- v4 CSVs: semicolon, UTF-8 with BOM; `site_key` is the coordinate alone.
- The FSRAR registry is streamed out of its ZIP, never loaded whole; the
  workflow cuts it to the cities of `deliveries.tsv` (`data/temp/egais/registry.tsv`).
