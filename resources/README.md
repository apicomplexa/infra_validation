# resources/

External data the workflow reads but does not produce. Everything here except
this README is git-ignored; restore it from the project archive
(`../infrastructure_2026`, tag `archive-2026-10-30`) or re-download it.

| Path | Source / version | md5 |
|---|---|---|
| `egais/data-20260701t0000-structure-20190918t0000.zip` | FSRAR consolidated licence register, open data, snapshot 2026-07-01 (10.9 GB XML inside; streamed, never unpacked) | `0a0a596cd485949a48aa9ae7aecb295b` |
| `overture/overture_places_2026-10-21.0.parquet` | Overture Maps Places, release 2026-10-21.0, three-city box (`python -m infracheck.fetch.overture`) | `0173e0234037b2df10db9845f342a3ac` |
| `foursquare/fsq_places_2026-10-15.parquet` | FSQ OS Places, release 2026-10-15, gated HF dataset (`python -m infracheck.fetch.foursquare`) | `8b3811fe0982d96302e1779f3b0f3fad` |
| `cache/yandex/final_yandex_cache.jsonl` | our Yandex Maps reads for the final delivery, 2026-10-22…29; **state, appended by `pixi run yandex-fetch`** | — |
| `cache/yandex/v4_yandex_cache.jsonl` | our Yandex Maps reads for the v4 alcohol layer, 2026-09-22/23; **state, appended** | — |
| `golden/` | archive outputs the regression tests compare against (taken 2026-10-30, verified deterministic) | — |

The Yandex caches are snapshots: every line carries the date it was read, and
lines are keyed by the query (`search_url`). Never edit them by hand; delete a
line to have that query read again.
