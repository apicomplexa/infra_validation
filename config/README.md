# Configuration

Everything a user of this workflow is expected to edit lives here.

| File | Purpose |
|---|---|
| `config.yaml` | Directory layout (`pathvars`), snapshots (`resources`), and one block per category holding every threshold and text. Validated against `workflow/schemas/config.schema.yaml`. |
| `deliveries.tsv` | One row per delivery file to check. Validated against `workflow/schemas/deliveries.schema.yaml`. |

Execution settings (cores, memory, cluster executor) are **not** configured
here — see `workflow/profiles/`.

## `deliveries.tsv` columns

| Column | Description |
|---|---|
| `delivery` | Unique id (letters, digits, `_`, `-`); the `{delivery}` wildcard and the `object_id` prefix. |
| `category` | Block of `categories:` that decides it (`catering`). |
| `city` | The city as the export writes it; also picks the registry settlements to parse. |
| `format` | Input adapter, `infracheck.inputs.<format>` (`final_xlsx`). |
| `path` | The delivered file, relative to the repository root. Only read. |
| `yandex_region` | Yandex Maps region path (`20/arkhangelsk`, `10849/severodvinsk`; Новодвинск goes through Arkhangelsk's). |

Row order is the order of the combined summary.

## `config.yaml` keys

| Key | Description |
|---|---|
| `deliveries` | Path to the delivery sheet. |
| `pathvars.*` | Directories referenced in rules as `<name>` (e.g. `<results>/x.tsv`). |
| `resources.egais_zip` | FSRAR registry ZIP; the `egais_register` rule streams it, never unpacks it. |
| `resources.overture`, `resources.foursquare` | Place snapshots (parquet). |
| `resources.yandex_cache.<name>` | Yandex answer caches (JSONL). The main DAG only reads them; `pixi run yandex-fetch` appends. |
| `yandex_fetch.limit`, `yandex_fetch.sleep` | Options of `pixi run yandex-fetch`: pages per delivery (0 = all), courtesy pause in seconds. |
| `notify.telegram` | Send start/success/failure messages to Telegram (needs `TG_BOT_TOKEN`, `TG_CHAT_ID`). |
| `categories.<name>` | One category: see below. |

### `categories.<name>`

`src/infracheck/categories/base.py` applies a block and holds no
category-specific code; `catering.py` only names its block.

| Key | Read by | Description |
|---|---|---|
| `checks` | workflow, rule | Checks run (one rule and one Evidence file each), in stacking order. |
| `yandex_cache` | `check_yandex` | Which `resources.yandex_cache` entry our query reads. |
| `policy.<check>` | the check | The fields of that check's `Policy` dataclass (`infracheck.checks.<check>.Policy`); an unknown key is an error. `policy.egais` is special: `mode` picks the check's rule (`catering`), `params` overrides `licmatch.MatchParams`, the rest are `licmatch.Policy` switches (`require_in_force`, `name_policy`, `name_fields`, `unit`, `home_cities`). |
| `existence.min_sources` | rule | «Подтверждено» = 1 at this many independent groups. |
| `existence.count` | rule | The axes a row needs to count (`identity`, `record_state`). |
| `existence.never_count` | rule | Sources that never count (the export's own name). |
| `existence.groups` | rule | Independence groups: `label` (in «Источники» / «Основания»), `column` (the `src_*` flag), `sources`, and optionally `checks` (clause order inside the group). |
| `existence.exclude` | rule | Records an aggregator built only from another group's source: `source`, `only` (datasets), `ignore` (the aggregator's own name), `sep`. A row of `source` whose `origin` tokens, minus `ignore`, are all in `only` is dropped silently if it would have counted. |
| `reasons.*` | rule | Separators, the « \|\| против/не найдено: » intro, the "no sources" text, and `con_order` (by check) of the not-counted clauses. |
| `query` | `yandex_queue` | Which objects our own query could still decide: fewer than `min_sources` groups besides `group`, and no counted row of `skip_checks`; written as `column` of the decided table. |
| `how` | rule | A "how was it reached" text (`yandex_how`): per check, the row field (`key`) whose value picks a text. |
| `derived` | rule | Extra columns of the decided table, as Python expressions over its columns. |
| `kind` | rule | «Вид?»: `column`, `reasons_column`, `vote_identity` (which identities vote), `votes` (scheme, label override, checks, `keep_empty`, in order), `words` (yes / no / vending / conflict / none), `schemes` (a `ladder` of `[role, regex]` read top down — **order is the rule** — or a `split` label list with `any_yes` / `all_no_top`). Quote `"yes"` / `"no"`: bare, YAML reads them as booleans. |
| `deliver` | `deliver` | The XLSX: output `suffix`, `sheet`, `legend_sheet`, `front` columns (`write: false` writes the header only), `appended` `[title, column]` pairs, and `legend` texts. |
| `deliver.legend` | `deliver` | `str.format` templates over the Policies used — `{providers.relink_m:.0f}`, `{foursquare.fresh:.4}` — plus `{min_sources}` and `{min_sources_genitive}`. No threshold is written out by hand. |
| `summary`, `combinations_title` | `decide`, `deliver` | «Итоги» lines: a fixed `value` (`rows`, `objects`, `confirmed_objects`, `confirmed_rows`), a `count` (boolean expression over the decided table; `min_sources` is in scope), or `counts` (several, " / "-joined). `for: [...]` repeats a line, `{}` replaced by each item. |

## Deviations from the three-axes default (kept for parity)

Where the config reproduces the archive against the default rule
(`docs/ARCHITECTURE.md`, «Три оси»), it says so in a `PARITY` comment:

- `kind.vote_identity: [strong, weak]` — FSQ / Overture records another object
  won (`exclusive` → weak) still vote on the kind, as in the archive.

Fixed on 2026-10-30 (`docs/changes/2026-10-30-overture-fsq-and-food-column.md`):
the Foursquare-only Overture place no longer counts (`existence.exclude`), and
«Общепит?» is written into the delivered sheet.
