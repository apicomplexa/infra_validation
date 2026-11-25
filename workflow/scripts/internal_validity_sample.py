"""Internal-validity sample: blind packets of the pipeline's match decisions.

The question is *internal* correctness: given what the pipeline's own sources
say (the Yandex cache, the Overture / Foursquare snapshots, the FSRAR extract,
the contractor's quality sheet), did it accept and reject the right record?
Whether the object exists on the ground is §8 of the methodology, not this.

The unit is one decision «object ↔ source record».  Per source two kinds of
stratum: *accepted* (the record counted as this object) and *rejected though a
candidate was there* (the best candidate the rule turned down), plus state
strata where the record matched but its state kept it out (Yandex «Больше не
работает», own archived licence).  All nine finalest deliveries are pooled;
the city is a field.  Population counts per stratum go into the manifest so
the judges' labels can be reweighted.

Read-only: `data/temp/*` (ObjectTable + Evidence of the last run), the raw
XLSX (quality sheet), `resources/` (snapshots, caches).  Nothing in the
pipeline is called that writes.  Deterministic: the order is a SHA-256 rank of
(seed, stratum, decision key), the shuffle a seeded `random.Random`.

Usage: pixi run python workflow/scripts/internal_validity_sample.py [--force]
       [--overlay config/finalest.yaml --out results/finalest/internal_validity_v2]

`--overlay` deep-merges a run's config overlay over `config/config.yaml`, so the
Yandex policy (one card one site, the contractor's hit withdrawn on a closed
card) is the one the run used; `--out` keeps an earlier sealed sample intact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from infracheck.checks import _directory as D
from infracheck.checks import yandex as Y
from infracheck.core.geo import dist_matrix, haversine_m
from infracheck.core.names import NAME_MIN, name_similarity, similar
from infracheck.inputs import final_xlsx

ROOT = Path(__file__).resolve().parents[2]
DELIVERIES = ROOT / "config" / "deliveries_finalest.tsv"
CONFIG = ROOT / "config" / "config.yaml"
TEMP = ROOT / "data" / "temp"
OBJECTS_CSV = ROOT / "results" / "finalest" / "{cat}_objects.csv"
REGISTRY = TEMP / "egais" / "registry.tsv"
OUT = ROOT / "results" / "finalest" / "internal_validity"

SEED = 20261122
N_PACKETS = 4
NEAR_SIM = 50.0  # a rejected candidate at least this alike is a "near miss" sub-stratum
PAGE_CONTEXT = 8  # other organisations of a Yandex page shown beside the candidate

# Sample size per stratum (truncated to the population).  Rejected strata are
# split by how alike the best turned-down candidate is: near misses are where a
# false rejection would hide, so they are over-sampled; weights undo it.
TARGET = {
    "yandex_accepted": 28,
    "yandex_rejected_near": 16,
    "yandex_rejected_other": 8,
    "yandex_closed": 10,
    "yandex_contractor_accepted": 5,
    "yandex_contractor_rejected": 5,
    "yandex_contractor_withdrawn": 5,
    "merge_unlike_names": 20,
    "merge_alike_names": 9,
    "relink_accepted": 10,
    "relink_rejected": 5,
    "fsq_accepted": 28,
    "fsq_rejected_near": 14,
    "fsq_rejected_low": 4,
    "fsq_rejected_exclusive": 2,
    "overture_accepted": 24,
    "overture_rejected_near": 7,
    "overture_rejected_confidence": 2,
    "overture_rejected_low": 2,
    "overture_rejected_exclusive": 2,
    "egais_accepted": 28,
    "egais_rejected": 26,
    "egais_inactive": 5,
}

SOURCE_RU = {
    "yandex": "Яндекс Карты (наша проверка: страница поиска)",
    "yandex_contractor": "Яндекс Карты (проверка подрядчика: карточки по адресу)",
    "merge": "Склейка подрядчика: записи OSM и 2GIS, сведённые в одну строку выгрузки",
    "relink": "Соседняя запись той же выгрузки от другого провайдера (OSM / 2GIS)",
    "fsq": "Foursquare OS Places (снимок 2026-10-15)",
    "overture": "Overture Places (снимок 2026-10-21)",
    "egais": "Реестр лицензий ФСРАР / ЕГАИС (снимок 2026-07-01)",
}
CATEGORY_RU = {"catering": "общепит", "alcohol": "алкоголь", "tobacco": "табак"}

# The judges' instructions, written next to the packets.  Russian, like every
# user-facing text of the project.  It names no threshold of the pipeline.
JUDGE_PROMPT = """\
# Судья: совпадает ли запись источника с объектом

Каждая строка файла `packets_N.jsonl` — один **пакет**: объект из выгрузки
подрядчика (`object`) и одна запись-кандидат из источника проверки
(`candidate`), иногда с контекстом (`context`). Поле `source` говорит, что это
за источник. Нужно ответить на два вопроса о кандидате.

Отвечай **только по данным пакета**. Ссылки не открывай, в сети ничего не
ищи. Из общих знаний допустимы только транслитерация, обычные
слова-дескрипторы и то, какое юрлицо держит какую известную сеть. Конкретных
фактов о самом объекте из памяти не добавляй.

## Формат ответа

На каждый пакет одна строка JSON, без другого текста:

```json
{"decision_id": "IV-…", "same_org": "yes", "open": "unclear", "reason": "одна фраза"}
```

- `same_org`: `yes` / `no` / `unclear`;
- `open`: `open` / `closed` / `unclear`;
- `reason`: одна короткая фраза по-русски, на чём основан ответ.

Ответь на **все** пакеты файла в любом порядке; `decision_id` копируй точно.

## same_org: кандидат — та же организация в той же точке?

**yes**, если кандидат описывает именно этот объект:

- транслитерация и разное написание: «Millstream» / «Мильстрим», «Lа-ваш» / «Лаваш»;
- дескриптор добавлен или убран: «кафе X» / «X», «X, магазин» / «X»,
  «Гастробар X» / «X»;
- владелец лицензии — известный оператор этой вывески: АО «Тандер» → «Магнит»,
  ООО «Агроторг» → «Пятёрочка», ООО «Альбион-2002» → «Бристоль» и т. п.;
- имя владельца лицензии или название заведения в адресе места
  («…, кафе "Холмс"») совпадает с вывеской объекта.

**no**, если кандидат — другая организация:

- **другой филиал** той же сети (другой адрес, заметно дальше);
- **соседняя организация** по тому же адресу: торговый центр, общий дом,
  фудкорт, другой этаж с другой вывеской;
- совпадает только общее слово («Продукты», «Кафе», «Столовая») при разных
  названиях или разном виде деятельности.

**unclear** — только если данных пакета **действительно не хватает**: обе
вывески общие и ничего их не различает; владелец лицензии — юрлицо с ничего не
говорящим названием, без названия заведения в адресе, и нет признака, что это
оператор вывески. Не ставь `unclear` из осторожности, если пакет позволяет
решить.

Расстояние: одна точка — обычно десятки метров. Координаты реестра ЕГАИС
округлены (~30 м) и бывают пустыми; координаты Foursquare и Overture бывают
сдвинуты. Большое расстояние при совпадающем адресе — не довод против; другой
адрес при одинаковом имени — довод в пользу другого филиала.

Особые виды пакетов:

- **Яндекс, наша проверка.** Решай про `candidate`; `other_organisations_on_page` —
  остальные организации той же страницы, для контекста. Если кандидат не тот,
  а другой из списка явно тот — ответ `no`, и назови того в `reason`.
- **Яндекс, проверка подрядчика.** Кандидат — имя из карточек, которые
  подрядчик нашёл по адресу; координат нет. Решай, наш ли это объект.
- **Склейка подрядчика (OSM + 2GIS).** Вопрос в `question_override`: все
  записи, сведённые в одну строку, — одна организация? `yes` — имена являются
  вариантами одного названия; `no` — хотя бы одна запись явно другая организация.
- **Соседняя запись выгрузки.** Кандидат — другая строка той же выгрузки от
  другого провайдера: это та же организация, записанная дважды?

## open: что запись-кандидат говорит о своём состоянии

Только по данным пакета и только о **записи**, а не о том, что ты знаешь об объекте.

- **closed** — источник прямо отмечает закрытие: у Яндекса «Больше не работает»
  или «Временно не работает» (`marked_*` = true, заголовок страницы);
  у Foursquare заполнено `date_closed`; лицензия ЕГАИС не действует
  (статус «истекла», «аннулирована», «приостановлена» и т. п.).
- **open** — запись прямо говорит, что организация действует на дату снимка:
  карточка Яндекса без отметок о закрытии; лицензия со статусом «действующая»;
  запись Foursquare без `date_closed`, обновлённая в последние несколько лет;
  у Overture `operating_status` = open.
- **unclear** — нет ни того, ни другого: старая запись Foursquare без отметок;
  Overture без `operating_status` (`update_time` — дата сборки выпуска, а не
  проверка места); склейки и соседние записи выгрузки, у которых статуса нет
  вовсе; карточки подрядчика.

`open` отвечает про запись-кандидат и тогда, когда `same_org` = `no`.
"""


# ----------------------------------------------------------------- helpers


def rank(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fnum(v, nd=1):
    try:
        v = float(v)
    except TypeError, ValueError:
        return None
    return None if not np.isfinite(v) else round(v, nd)


def s(v) -> str:
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v) else str(v).strip()


def obj_block(r: pd.Series) -> dict:
    return {
        "name": s(r["name"]),
        "address": s(r["address"]),
        "city": s(r["city"]),
        "lat": fnum(r["lat"], 6),
        "lon": fnum(r["lon"], 6),
        "claimed_category": f"{CATEGORY_RU[r['category']]}: {s(r.get('types'))}".rstrip(": "),
    }


def metres(r: pd.Series, lat, lon):
    if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
        return None
    return round(float(haversine_m(float(r["lat"]), float(r["lon"]), float(lat), float(lon))), 1)


class Collector:
    """Decisions by stratum, deduplicated on a decision key."""

    def __init__(self):
        self.rows: list[dict] = []
        self.seen: set[tuple] = set()
        self.dupes = Counter()

    def add(self, stratum: str, key: tuple, meta: dict, packet: dict):
        k = (stratum.split("_")[0], *key)
        if k in self.seen:
            self.dupes[stratum] += 1
            return
        self.seen.add(k)
        self.rows.append({"stratum": stratum, "key": "|".join(map(str, key)), **meta, "_packet": packet})


def base_meta(r: pd.Series, oid: str, source: str, decision: str, ident: str, state: str) -> dict:
    return {
        "source": source,
        "delivery": r["delivery"],
        "category": r["category"],
        "city": r["city"],
        "object_id": oid,
        "merged": r.get("merged", ""),
        "pipeline_decision": decision,
        "identity": ident,
        "record_state": state,
    }


def obj_key(r: pd.Series) -> tuple:
    return (s(r["city"]), s(r["name"]), f"{float(r['lat']):.5f}", f"{float(r['lon']):.5f}")


# ----------------------------------------------------------------- loading


def deep_merge(base: dict, over: dict) -> dict:
    """Snakemake's --configfile semantics: nested dicts merge, anything else replaces."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_delivery(d: pd.Series):
    tdir = TEMP / d.delivery
    ob = pd.read_parquet(tdir / "objects.parquet")
    if "object_id" in ob.columns:
        ob = ob.set_index("object_id")
    oc = pd.read_csv(str(OBJECTS_CSV).format(cat=d.category), sep=";", encoding="utf-8-sig", low_memory=False)
    oc = oc[oc["delivery"] == d.delivery].set_index("object_id")
    ob = ob.join(oc[["merged"]], how="left")
    ev = {p.stem: pd.read_parquet(p) for p in sorted((tdir / "evidence").glob("*.parquet"))}
    return ob, ev


# ----------------------------------------------------------------- Yandex


def page_orgs(rec: dict) -> list:
    return [c for c in Y.Answer.from_dict(rec).candidates if not Y.is_toponym(c)]


def cand_dict(c, r: pd.Series, rec: dict | None = None, *, with_title=False) -> dict:
    out = {
        "name": c.name,
        "address": c.address,
        "lat": fnum(c.lat, 6),
        "lon": fnum(c.lon, 6),
        "distance_to_object_m": metres(r, c.lat, c.lon),
        "marked_closed_bolshe_ne_rabotaet": bool(c.closed),
        "marked_paused_vremenno_ne_rabotaet": bool(c.paused),
    }
    if with_title and rec and c.oid and c.oid in (rec.get("canonical") or ""):
        out["card_page_title"] = rec.get("og_title", "")
        out["rubric"] = Y.rubric(rec, c.url)
    return out


def page_context(orgs, chosen, r: pd.Series) -> list[dict]:
    rest = [c for c in orgs if c is not chosen]
    rest.sort(key=lambda c: metres(r, c.lat, c.lon) if c.lat is not None else 1e12)
    return [
        {k: v for k, v in cand_dict(c, r).items() if k in ("name", "address", "distance_to_object_m")}
        | ({"marked_closed": True} if c.closed or c.paused else {})
        for c in rest[:PAGE_CONTEXT]
    ]


def yandex_decisions(col: Collector, ob: pd.DataFrame, ev: pd.DataFrame, caches: dict, cache_name: str, pol):
    cache = caches[cache_name]
    for _, e in ev[ev["check"] == "yandex"].iterrows():
        oid = e["object_id"]
        r = ob.loc[oid]
        rec = cache.get(e["yandex_search_url"])
        if rec is None or rec.get("note"):
            continue
        orgs = page_orgs(rec)
        st = e["yandex_status"]
        if st in (Y.STATUS_PRESENT, Y.STATUS_CLOSED):
            chosen = next((c for c in orgs if c.url and c.url == e["ref"]), None)
            if chosen is None:
                chosen = next((c for c in orgs if c.name == e["matched_name"]), None)
            if chosen is None:
                continue
            stratum = "yandex_accepted" if st == Y.STATUS_PRESENT else "yandex_closed"
            decision = "accepted" if st == Y.STATUS_PRESENT else "matched_closed"
            rule = "the card the check accepted"
        elif st in (Y.STATUS_ABSENT, Y.STATUS_AMBIGUOUS):
            located = [c for c in orgs if c.lat is not None and c.lon is not None]
            if not located:
                continue
            scored = [(name_similarity(str(r["name"]), c.name), metres(r, c.lat, c.lon), c) for c in located]
            best = max(scored, key=lambda t: (t[0], -t[1]))
            if best[0] >= NEAR_SIM:
                chosen, stratum = best[2], "yandex_rejected_near"
                rule = f"most alike organisation on the page (name similarity >= {NEAR_SIM:g})"
            else:
                chosen = min(scored, key=lambda t: t[1])[2]
                stratum = "yandex_rejected_other"
                rule = "nearest organisation on the page (none alike)"
            decision = "rejected"
        else:
            continue
        sim = name_similarity(str(r["name"]), chosen.name)
        meta = base_meta(r, oid, "yandex", decision, e["identity"], e["record_state"]) | {
            "yandex_status": st,
            "candidate_ref": chosen.url,
            "candidate_name": chosen.name,
            "distance_m": metres(r, chosen.lat, chosen.lon),
            "name_score": round(sim, 1),
            "selection_rule": rule,
            "checked": rec.get("checked", ""),
        }
        packet = {
            "source": SOURCE_RU["yandex"],
            "object": obj_block(r),
            "candidate": cand_dict(chosen, r, rec, with_title=True),
            "context": {
                "query": rec.get("search_url", ""),
                "page_read_on": rec.get("checked", ""),
                "other_organisations_on_page": page_context(orgs, chosen, r),
            },
        }
        col.add(stratum, ("yandex", e["yandex_search_url"], chosen.oid or chosen.name), meta, packet)

    # The contractor's «Найдено», re-judged by name (no coordinates are kept).
    con = ev[ev["check"] == "yandex_contractor"]
    counted = set(con.loc[con["identity"] == "strong", "object_id"])
    own_closed = ev[(ev["check"] == "yandex") & (ev["yandex_status"] == Y.STATUS_CLOSED)]
    closed_on = dict(zip(own_closed["object_id"], own_closed["yandex_checked"].astype(str), strict=True))
    for oid, r in ob.iterrows():
        rows = json.loads(r.get("yandex_contractor_rows") or "[]")
        if not rows:
            continue
        c = Y.rejudge_contractor(r["yandex_contractor_rows"], str(r["name"]), pol)
        cards = []
        for names_s, addr, url in rows:
            names = [n.strip() for n in str(names_s).split(";") if n.strip() and n.strip() != "nan"]
            cards.append({"names_at_address": names, "address": s(addr), "url": s(url)})
        allnames = [(similar(str(r["name"]), n), n) for cd in cards for n in cd["names_at_address"]]
        if not allnames:
            continue
        sim, best = max(allnames)
        # The run's verdict, not a re-judgement: with `closed_overrides_contractor` a name match on a
        # card our query read closed is withdrawn (a state decision, like `yandex_closed`).
        if c["ok"] and oid not in counted:
            stratum, decision, ident = "yandex_contractor_withdrawn", "matched_closed", "weak"
        elif c["ok"]:
            stratum, decision, ident = "yandex_contractor_accepted", "accepted", "strong"
        else:
            stratum, decision, ident = "yandex_contractor_rejected", "rejected", "weak"
        meta = base_meta(r, oid, "yandex_contractor", decision, ident, "current") | {
            "candidate_ref": c["url"] or cards[0]["url"],
            "candidate_name": best,
            "distance_m": None,
            "name_score": round(sim, 1),
            "selection_rule": "most alike name among the contractor's found cards",
        }
        packet = {
            "source": SOURCE_RU["yandex_contractor"],
            "object": obj_block(r),
            "candidate": {"name": best, "note": "координат у карточки подрядчика нет"},
            "context": {"contractor_found_cards": cards}
            | (
                {"our_query": f"та же карточка в нашем запросе {closed_on.get(oid, '')}: «Больше не работает»"}
                if stratum == "yandex_contractor_withdrawn"
                else {}
            ),
        }
        col.add(stratum, ("yandex_contractor", *obj_key(r), best), meta, packet)


# ----------------------------------------------------------------- OSM / 2GIS


def merge_decisions(col: Collector, d: pd.Series, ob: pd.DataFrame):
    path = ROOT / d.path
    df = final_xlsx.read_rows(path)
    orig = pd.read_excel(path, sheet_name="original_data")
    qual = pd.read_excel(path, sheet_name="quality")
    key = ["type_orig", "name_orig", "adress_orig", "latitude", "longitude"]
    q = orig[key].merge(
        qual[key + ["providers", "matched_source_ids", "original_names", "match_distance_meters"]],
        on=key,
        how="left",
        validate="1:1",
    )
    if not (q["matched_source_ids"].fillna("").to_numpy() == df["_ids"].to_numpy()).all():
        raise RuntimeError(f"{path.name}: quality join disagrees with read_rows")
    rowmap = pd.read_parquet(TEMP / d.delivery / "rowmap.parquet")
    q["object_id"] = rowmap["object_id"].to_numpy()
    prov = q["providers"].fillna("")
    both = q[prov.str.contains("osm") & prov.str.contains("2gis")]
    for oid, g in both.groupby("object_id", sort=True):
        r = ob.loc[oid]
        ids = sorted({t.strip() for v in g["matched_source_ids"].fillna("") for t in str(v).split(";") if t.strip()})
        names = list(
            dict.fromkeys(t.strip() for v in g["original_names"].fillna("") for t in str(v).split("; ") if t.strip())
        )
        dist = pd.to_numeric(g["match_distance_meters"], errors="coerce").max()
        # The contractor's names differ by a descriptor ("Магнит" / "Магнит, супермаркет") far more
        # often than by the business, so the split is by name likeness, not by count.
        own = s(r["name"])
        others = [n for n in names if n != own] or names[1:]
        low = min((max(similar(own, n), similar(n, own)) for n in others), default=100.0)
        stratum = "merge_unlike_names" if low < NAME_MIN else "merge_alike_names"
        meta = base_meta(r, oid, "merge", "accepted", "strong", "current") | {
            "candidate_ref": "; ".join(ids),
            "candidate_name": " | ".join(names),
            "distance_m": fnum(dist),
            "name_score": round(low, 1),
            "selection_rule": "every OSM+2GIS row of the object (contractor's merge)",
            "n_source_records": len(ids),
        }
        packet = {
            "source": SOURCE_RU["merge"],
            "object": obj_block(r),
            "candidate": {
                "merged_record_names": names,
                "n_osm_records": sum(i.startswith("osm") for i in ids),
                "n_2gis_records": sum(i.startswith("2gis") for i in ids),
                "max_distance_between_records_m": fnum(dist),
                "note": "имена — множество различных написаний; какое имя какому провайдеру принадлежит, не указано",
            },
            "question_override": "Записи, сведённые подрядчиком в одну строку, — одна организация?",
        }
        col.add(stratum, ("merge", tuple(ids)), meta, packet)


def relink_decisions(col: Collector, ob: pd.DataFrame, ev: pd.DataFrame, pol: dict):
    rl = ev[ev["check"] == "relink"]
    linked = {(e.object_id, e.source) for e in rl.itertuples()}
    for e in rl.itertuples():
        a, b = ob.loc[e.object_id], ob.loc[e.relink_object]
        meta = base_meta(a, e.object_id, "relink", "accepted", "strong", "current") | {
            "candidate_ref": e.relink_object,
            "candidate_name": s(b["name"]),
            "distance_m": fnum(e.distance_m),
            "name_score": round(min(similar(a["name"], b["name"]), similar(b["name"], a["name"])), 1),
            "selection_rule": f"twin that lent {e.source}",
        }
        col.add("relink_accepted", ("relink", *sorted([obj_key(a), obj_key(b)])), meta, relink_packet(a, b))

    flag = {"osm": "osm", "2gis": "gis"}
    for _, part in ob.groupby("city"):
        idx = part.index.to_numpy()
        dm = dist_matrix(part["lat"].to_numpy(float), part["lon"].to_numpy(float))
        np.fill_diagonal(dm, np.inf)
        for ap, a_id in enumerate(idx):
            a = ob.loc[a_id]
            for src, fl in flag.items():
                if bool(a[fl]) or (a_id, src) in linked:
                    continue
                cand = np.where((dm[ap] <= pol["relink_m"]) & part[fl].to_numpy(bool))[0]
                if not len(cand):
                    continue
                scored = []
                for bp in cand:
                    b = ob.loc[idx[bp]]
                    scored.append((min(similar(a["name"], b["name"]), similar(b["name"], a["name"])), -dm[ap, bp], bp))
                sim, negd, bp = max(scored)
                b = ob.loc[idx[bp]]
                meta = base_meta(a, a_id, "relink", "rejected", "none", "unknown") | {
                    "candidate_ref": idx[bp],
                    "candidate_name": s(b["name"]),
                    "distance_m": round(-negd, 1),
                    "name_score": round(sim, 1),
                    "selection_rule": f"most alike {src} record of the export within {pol['relink_m']:g} m",
                }
                col.add("relink_rejected", ("relink", *sorted([obj_key(a), obj_key(b)])), meta, relink_packet(a, b))


def relink_packet(a: pd.Series, b: pd.Series) -> dict:
    return {
        "source": SOURCE_RU["relink"],
        "object": obj_block(a),
        "candidate": {
            "name": s(b["name"]),
            "address": s(b["address"]),
            "lat": fnum(b["lat"], 6),
            "lon": fnum(b["lon"], 6),
            "distance_to_object_m": metres(a, b["lat"], b["lon"]),
            "type_in_export": s(b.get("types")),
        },
    }


# ----------------------------------------------------------------- Overture / FSQ


def directory_decisions(col, ob, ev, places, which: str, pol: dict):
    """Accepted rows from Evidence; rejected = best candidate in the check's radius."""
    src = "fsq" if which == "foursquare" else "overture"
    idcol = "fsq_place_id" if src == "fsq" else "id"
    pos = {v: i for i, v in enumerate(places[idcol])}
    e_all = ev.get(which, pd.DataFrame(columns=["object_id"]))
    has_row = set(e_all["object_id"])
    for e in e_all.itertuples():
        r = ob.loc[e.object_id]
        j = pos.get(e.ref)
        if e.identity == "strong" and e.record_state == "current":
            stratum, decision = f"{src}_accepted", "accepted"
        elif e.identity == "weak":
            stratum, decision = f"{src}_rejected_exclusive", "rejected_exclusive"
        else:
            continue  # FSQ stale / closed: a state decision, not sampled
        if j is None:
            continue
        meta = base_meta(r, e.object_id, src, decision, e.identity, e.record_state) | {
            "candidate_ref": e.ref,
            "candidate_name": e.matched_name,
            "distance_m": fnum(e.distance_m),
            "name_score": round(similar(r["name"], e.matched_name), 1),
            "selection_rule": "record the check matched",
            "origin": getattr(e, "origin", ""),
        }
        col.add(stratum, (src, *obj_key(r), e.ref), meta, place_packet(src, r, places.iloc[j]))
    near_m, generic_m = pol["near_m"], pol["generic_m"]
    conf = places["confidence"].fillna(0).to_numpy(float) if src == "overture" else None
    for oid, r in ob.iterrows():
        if oid in has_row:
            continue
        cand, dist = D.candidates(r["lat"], r["lon"], bool(r["generic"]), places, near_m=near_m, generic_m=generic_m)
        if not len(cand):
            continue
        scored = [(similar(r["name"], places.at[j, "name"]), -dist[j], j) for j in cand]
        # Overture's own confidence floor removes a place before the name is
        # looked at: an alike-named place below it is a rejection of the record,
        # not of the identity, and has a stratum of its own.
        lowconf = [t for t in scored if conf is not None and conf[t[2]] < pol["conf_min"]]
        if lowconf and max(lowconf)[0] >= pol["name_min"]:
            sim, negd, j = max(lowconf)
            stratum = f"{src}_rejected_confidence"
        else:
            scored = [t for t in scored if t not in lowconf]
            if not scored:
                continue
            sim, negd, j = max(scored)
            stratum = f"{src}_rejected_near" if sim >= NEAR_SIM else f"{src}_rejected_low"
        meta = base_meta(r, oid, src, "rejected", "none", "unknown") | {
            "candidate_ref": places.at[j, idcol],
            "candidate_name": places.at[j, "name"],
            "distance_m": round(-negd, 1),
            "name_score": round(sim, 1),
            "selection_rule": "most alike place within the check's radius",
            "origin": places.at[j, "datasets"] if src == "overture" else "",
            "overture_confidence": round(float(conf[j]), 2) if conf is not None else None,
        }
        col.add(stratum, (src, *obj_key(r), places.at[j, idcol]), meta, place_packet(src, r, places.iloc[j]))


def place_packet(src: str, r: pd.Series, p: pd.Series) -> dict:
    c = {
        "name": s(p["name"]),
        "address": s(p.get("address")),
        "locality": s(p.get("locality")),
        "lat": fnum(p["lat"], 6),
        "lon": fnum(p["lon"], 6),
        "distance_to_object_m": metres(r, p["lat"], p["lon"]),
    }
    if src == "fsq":
        c |= {
            "categories": s(p["categories"]),
            "date_created": s(p.get("date_created")),
            "date_refreshed": s(p["date_refreshed"]),
            "date_closed": s(p["date_closed"]),
        }
    else:
        c |= {
            "category": s(p.get("category")),
            "operating_status": s(p.get("operating_status")),
            "datasets": s(p.get("datasets")) or s(p.get("dataset")),
            "update_time": s(p.get("update_time")),
        }
    return {"source": SOURCE_RU[src], "object": obj_block(r), "candidate": c}


# ----------------------------------------------------------------- EGAIS


def egais_decisions(col, ob, ev, registry):
    e_all = ev.get("egais")
    if e_all is None:
        return
    for e in e_all[e_all["ref"].fillna("").ne("")].itertuples():
        r = ob.loc[e.object_id]
        if e.identity == "strong" and e.record_state == "current":
            stratum, decision = "egais_accepted", "accepted"
        elif e.identity == "strong" and e.record_state == "inactive":
            stratum, decision = "egais_inactive", "matched_inactive"
        elif e.identity == "weak":
            stratum, decision = "egais_rejected", "rejected"
        else:
            continue
        reg = registry[(registry["licence_no"] == e.ref) & (registry["outlet_address"] == e.matched_address)]
        if reg.empty:
            reg = registry[registry["licence_no"] == e.ref]
        if reg.empty:
            continue
        g = reg.iloc[0]
        plaus = str(g.get("coord_plausible")).lower() == "true"
        cand = {
            "holder": s(g["org_name"]),
            "holder_inn": s(g["org_inn"]),
            "outlet_address": s(g["outlet_address"]),
            "activity": s(g["activity"]),
            "licence_no": s(g["licence_no"]),
            "licence_status": s(g["status"]),
            "date_issued": s(g["date_issued"]),
            "date_expires": s(g["date_expires"]),
            "status_changed": s(g["status_changed"]),
            "status_reason": s(g["status_reason"]),
            "lat": fnum(g["lat"], 6) if plaus else None,
            "lon": fnum(g["lon"], 6) if plaus else None,
            "distance_to_object_m": fnum(e.distance_m),
            "note": "реестр называет владельца лицензии, не магазин; координаты реестра округлены (~30 м)",
        }
        code = getattr(e, "egais_status_code", "")
        meta = base_meta(r, e.object_id, "egais", decision, e.identity, e.record_state) | {
            "candidate_ref": e.ref,
            "candidate_name": s(g["org_name"]),
            "distance_m": fnum(e.distance_m),
            "name_score": fnum(e.name_score),
            "selection_rule": f"licence the join cited (status code {code})",
            "egais_status_code": code,
        }
        packet = {"source": SOURCE_RU["egais"], "object": obj_block(r), "candidate": cand}
        col.add(stratum, ("egais", s(r.get("merged")), r["delivery"], e.ref, s(g["outlet_address"])), meta, packet)


# ----------------------------------------------------------------- main


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return ""


def main(argv=None) -> int:
    global OUT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="overwrite an existing sealed sample")
    ap.add_argument("--overlay", type=Path, help="config overlay of the run (e.g. config/finalest.yaml)")
    ap.add_argument("--out", type=Path, default=OUT, help="output folder (default: the first sample's)")
    args = ap.parse_args(argv)
    OUT = args.out if args.out.is_absolute() else ROOT / args.out
    if (OUT / "manifest.json").exists() and not args.force:
        raise SystemExit(f"{OUT / 'manifest.json'} exists: the sample is sealed (use --force deliberately)")

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if args.overlay:
        overlay = args.overlay if args.overlay.is_absolute() else ROOT / args.overlay
        cfg = deep_merge(cfg, yaml.safe_load(overlay.read_text(encoding="utf-8")))
    deliveries = pd.read_csv(DELIVERIES, sep="\t")
    caches = {k: Y.load_cache(ROOT / v) for k, v in cfg["resources"]["yandex_cache"].items()}
    import duckdb

    overture = duckdb.sql(f"SELECT * FROM '{ROOT / cfg['resources']['overture']}'").df()
    overture["name"] = overture["name"].fillna("").astype(str)
    overture = overture[overture["name"].ne("")].reset_index(drop=True)
    fsq = duckdb.sql(f"SELECT * FROM '{ROOT / cfg['resources']['foursquare']}'").df()
    fsq["name"] = fsq["name"].fillna("").astype(str)
    for c in ("date_refreshed", "date_closed", "categories"):
        fsq[c] = fsq[c].fillna("").astype(str)
    fsq = fsq[fsq["name"].ne("")].reset_index(drop=True)
    registry = pd.read_csv(REGISTRY, sep="\t", dtype=str, keep_default_na=False)

    col = Collector()
    for d in deliveries.itertuples():
        ob, ev = load_delivery(d)
        pol = cfg["categories"][d.category]["policy"]
        ypol = Y.Policy(**pol["yandex"])
        if "yandex" in ev:
            yandex_decisions(col, ob, ev["yandex"], caches, cfg["categories"][d.category]["yandex_cache"], ypol)
        merge_decisions(col, d, ob)
        relink_decisions(col, ob, ev["providers"], pol["providers"])
        directory_decisions(col, ob, ev, overture, "overture", pol["overture"])
        directory_decisions(col, ob, ev, fsq, "foursquare", pol["foursquare"])
        if d.category in ("catering", "alcohol"):  # tobacco: EGAIS is never a source
            egais_decisions(col, ob, ev, registry)

    pop = pd.DataFrame(col.rows)
    pop["rank"] = [rank(SEED, st, k) for st, k in zip(pop["stratum"], pop["key"], strict=True)]
    pop = pop.sort_values(["stratum", "rank"]).reset_index(drop=True)
    pop_n = pop["stratum"].value_counts().to_dict()
    unknown = set(pop_n) - set(TARGET)
    if unknown:
        raise RuntimeError(f"strata without a target: {sorted(unknown)}")
    pop["rank_in_stratum"] = pop.groupby("stratum").cumcount() + 1
    pop["n_target"] = pop["stratum"].map(TARGET)
    smp = pop[pop["rank_in_stratum"] <= pop["n_target"]].copy()
    n_smp = smp["stratum"].value_counts().to_dict()
    smp["pop_n"] = smp["stratum"].map(pop_n)
    smp["sample_n"] = smp["stratum"].map(n_smp)
    smp["weight"] = (smp["pop_n"] / smp["sample_n"]).round(4)
    smp["decision_id"] = [
        "IV-" + rank(SEED, "id", st, k)[:10] for st, k in zip(smp["stratum"], smp["key"], strict=True)
    ]
    if smp["decision_id"].duplicated().any():
        raise RuntimeError("decision_id collision")

    # Packets: balanced round-robin over the stratum-sorted list, then shuffled per file.
    rng = random.Random(SEED)
    order = smp.sort_values(["stratum", "rank"]).index.tolist()
    offset = rng.randrange(N_PACKETS)
    smp.loc[order, "packet"] = [((i + offset) % N_PACKETS) + 1 for i in range(len(order))]
    smp["packet"] = smp["packet"].astype(int)

    OUT.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for k in range(1, N_PACKETS + 1):
        part = smp[smp["packet"] == k].sort_values("rank")
        idx = part.index.tolist()
        rng.shuffle(idx)
        path = OUT / f"packets_{k}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for i in idx:
                pk = smp.at[i, "_packet"]
                fh.write(json.dumps({"decision_id": smp.at[i, "decision_id"], **pk}, ensure_ascii=False) + "\n")
        outputs[path.name] = sha256_file(path)

    (OUT / "judge_prompt.md").write_text(JUDGE_PROMPT, encoding="utf-8")
    outputs["judge_prompt.md"] = sha256_file(OUT / "judge_prompt.md")

    cols = [
        "decision_id",
        "packet",
        "stratum",
        "source",
        "pipeline_decision",
        "identity",
        "record_state",
        "delivery",
        "category",
        "city",
        "object_id",
        "merged",
        "candidate_ref",
        "candidate_name",
        "distance_m",
        "name_score",
        "selection_rule",
        "pop_n",
        "sample_n",
        "weight",
        "rank_in_stratum",
        "key",
    ]
    extra = [c for c in smp.columns if c not in cols and not c.startswith("_") and c not in ("rank", "n_target")]
    dec = smp[cols + extra].sort_values(["stratum", "rank_in_stratum"])
    dec.to_csv(OUT / "decisions.csv", index=False, encoding="utf-8-sig")
    outputs["decisions.csv"] = sha256_file(OUT / "decisions.csv")
    pop_cols = [
        "stratum",
        "key",
        "source",
        "pipeline_decision",
        "delivery",
        "category",
        "city",
        "object_id",
        "candidate_ref",
        "candidate_name",
        "distance_m",
        "name_score",
        "rank_in_stratum",
    ]
    pop[pop_cols].to_csv(OUT / "population.csv", index=False, encoding="utf-8-sig")
    outputs["population.csv"] = sha256_file(OUT / "population.csv")

    strata = {
        st: {
            "population": int(pop_n.get(st, 0)),
            "target": TARGET[st],
            "sample": int(n_smp.get(st, 0)),
            "weight": round(pop_n[st] / n_smp[st], 4) if n_smp.get(st) else None,
            "duplicates_dropped": int(col.dupes.get(st, 0)),
            "population_by_city": pop[pop["stratum"] == st]["city"].value_counts().sort_index().to_dict(),
            "sample_by_city": smp[smp["stratum"] == st]["city"].value_counts().sort_index().to_dict(),
        }
        for st in TARGET
    }
    inputs = [
        DELIVERIES,
        CONFIG,
        *([overlay] if args.overlay else []),
        REGISTRY,
        ROOT / cfg["resources"]["overture"],
        ROOT / cfg["resources"]["foursquare"],
    ]
    inputs += [ROOT / v for v in cfg["resources"]["yandex_cache"].values()]
    for d in deliveries.itertuples():
        inputs += [ROOT / d.path, TEMP / d.delivery / "objects.parquet", TEMP / d.delivery / "rowmap.parquet"]
        inputs += sorted((TEMP / d.delivery / "evidence").glob("*.parquet"))
    inputs += [Path(str(OBJECTS_CSV).format(cat=c)) for c in ("catering", "alcohol", "tobacco")]
    manifest = {
        "purpose": "internal validity of match decisions (object <-> source record), stage 1: sample + blind packets",
        "seed": SEED,
        "git_head": git_head(),
        "script": "workflow/scripts/internal_validity_sample.py",
        "unit": "one decision object <-> source record",
        "dedupe": "per source on (city, object name, coords to 1e-5, candidate record); the first occurrence in "
        "deliveries_finalest.tsv order wins (catering before risk layers, tobacco before alcohol), so a pair "
        "seen in two deliveries is one decision with the first delivery's verdict",
        "within_stratum_design": "simple random sample without replacement (SHA-256 rank of seed|stratum|key); "
        "weight = population / sample",
        "rejected_candidate_rule": "Yandex: most alike organisation on the page if similarity >= near_sim_split "
        "(stratum *_near), else the nearest (*_other); Overture/FSQ: most alike place within the check's radius, "
        "split at near_sim_split; Overture places below the confidence floor that pass the name floor form "
        "overture_rejected_confidence; relink: most alike other-provider record within relink_m",
        "near_sim_split": NEAR_SIM,
        "n_packets": N_PACKETS,
        "n_decisions": len(smp),
        "packet_sizes": smp["packet"].value_counts().sort_index().astype(int).to_dict(),
        "strata": strata,
        "blind_fields_excluded": [
            "pipeline decision",
            "stratum",
            "identity / record_state",
            "name scores",
            "thresholds",
            "Overture confidence",
            "selection rule",
        ],
        "not_sampled": {
            "fsq_stale_or_closed": "FSQ record matched but stale/closed: a state decision, never counted",
            "tobacco_egais": "EGAIS is never a source for tobacco (never_count); its matches are hints only",
            "arkh_admin": "column empty in all rows of all nine files: no decisions exist",
            "distance_rejections": "candidates outside the check's radius are not considered (only the radius the "
            "check itself searched)",
            "yandex_error_and_unread": "objects never queried (verdict did not depend on Yandex) or answered with "
            "an error have no decision",
            "merge_rejections": "the contractor's declined OSM/2GIS pairs are not in the delivery",
        },
        # A cache another class uses (nm) may be absent here; it is listed, not hashed.
        "inputs": {str(p.relative_to(ROOT)): sha256_file(p) if p.exists() else None for p in inputs},
        "outputs": outputs,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    for st in TARGET:
        print(f"{st:32s} pop {strata[st]['population']:6d}  sample {strata[st]['sample']:3d}")
    print(f"total sample {len(smp)}; packets {manifest['packet_sizes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
