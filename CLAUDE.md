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

## Four more categories: medicine, grocery, education, sport (`finalest_nm`)

Added by N. Mitkin's sessions on 2026-11-24 for the same final delivery of
Muslim (12 workbooks: 3 cities × 4 classes). Read before touching them:
`docs/changes/2026-11-24-nm-categories.md` (blocks, register flag, unified base)
and `docs/changes/2026-11-24-nm-yandex-all.md` (Yandex query for every object and
the matching rules found by checking against the supervisor's manual review).

- Config: blocks `categories.medicine|grocery|education|sport` in `config/config.yaml`
  (anchors `&nm_*`); deliveries `config/deliveries_finalest_nm.tsv`; overlay
  `config/finalest_nm.yaml` (results `results/finalest_nm/`, logs `logs/finalest_nm/`);
  Yandex cache `resources/cache/yandex/nm_yandex_cache.jsonl` (not in git: Yandex
  terms; back it up, never commit it).
- **Invariant:** everything these categories need beyond the archive's rule is an
  opt-in field of a check's `Policy` (default = old behaviour) or a config key the
  other categories do not set. Catering, alcohol and tobacco must not move; a change
  to shared code needs proof (unit tests plus a same-output check with their policies).
- Opt-in fields used here (`checks/yandex.py`): `contain_m`, `numbers`,
  `paused_is_closed`, `fallback_vague_address`, `prefer_open_m`, `exclusive_m`,
  `closed_overrides_contractor`, `address_kind`, `address_loose_m`; `query.all`
  (`categories/base.py`); `merge.shared_counted` on our Yandex card. Helpers:
  `core/names.py` (`contains_words`, `number_agreement`, `trade_kind`, `has_kind`),
  `core/address.py` (`address_key`, `same_address`, `same_building`).
- Snakemake does not track `src/` as a rule input: after editing `src/infracheck`
  without touching the config, rerun with `--forceall` (offline, ~30 s), or the old
  evidence stays. To see what a matching change moved, copy
  `data/temp/<delivery>/evidence/yandex.parquet` aside first and diff the statuses
  per object; every moved object gets read by a human before the change is kept.
- Never: let a match made by a lifted rule (number, containment, address) close an
  object; count the contractor's «Найдено» when our query read that card closed.
- `workflow/scripts/unified_base.py` — one row per merged object across deliveries,
  supervisor's flag names (`OSM`, `GIS`, `yandex`, ...), «В базу», «Почему не в базе»,
  «Что проверить вручную»; the name comes from the OSM record (the fascia), the
  other record names go to «Другие названия»; `--books DIR --stamp YYMMDD` writes one workbook per
  category (sheets База, Все объекты, На ручную проверку sorted by address, Этапы,
  Возможные дубли, Переменные). Works for any category.
- Windows without WSL: Docker Desktop, image `ghcr.io/prefix-dev/pixi:latest`, pixi
  env in volume `infra_pixi` mounted at `/work/.pixi`:
  `docker run --rm -v "<repo>:/work" -v infra_pixi:/work/.pixi -w /work ghcr.io/prefix-dev/pixi:latest bash -c "pixi run snakemake --cores 8 --configfile config/finalest_nm.yaml -- all"`;
  the network step: `... --cores 1 --configfile config/finalest_nm.yaml --forcerun yandex_fetch -- yandex_fetch`.
- Data in `data/cleaned/` of the parent project and every output carrying 2GIS or
  Yandex rows must not be published (licences not confirmed).
