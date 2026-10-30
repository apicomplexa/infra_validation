"""The fourth delivery (data/raw/v4): two risk-layer CSVs, alcohol and tobacco.

Ported from the archive's `arkhinfra.v4_common` (tag `archive-2026-10-30`) as far
as the licence join needs it -- the loader and the tier columns -- and kept for
regression of the alcohol / tobacco checks.  `category_2022` and `okrug_key` are
not carried: they need the v3 recoder, and no check reads them.

The fourth delivery is not a new survey.  It is a **risk-attribution layer** over
the third: two CSVs, one per risk factor, that re-tag objects already present in
the third export with a judgement about whether they plausibly sell alcohol or
tobacco.  Three things about it govern every use:

* **82 % of it is the `Возможная …` (inferred) tier** -- the object's type is
  compatible with selling the product, which the contractor's own
  `verification_comment` says *требует отдельной проверки*.  `evidence` keeps
  `Direct` and `Inferred` apart.
* **Off-trade and on-trade are licensed separately**, so `trade` splits them
  before anything is compared with a licence.
* **The two files overlap on 359 coordinates**: 1,694 rows, 1,335 sites.  A row
  is a record, not an object -- `objects()` keeps one row per record, as the
  licence join does, and the join's own entity rule decides which records are
  one shop.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from infracheck.core import objects as obj
from infracheck.core.names import is_generic

# `category_orig` carries exactly one value per file, and `type_orig` four.
# Neither vocabulary appears in the first three deliveries.
CATEGORY_TOBACCO = "Табачная и никотинсодержащая продукция"
CATEGORY_ALCOHOL = "Алкогольная продукция"

# tobacco
T_POSSIBLE = "Возможная продажа табачной продукции"
T_SPECIALISED = "Специализированная продажа табачной продукции"
T_VAPE = "Вейпы и никотинсодержащая продукция"
T_HOOKAH = "Кальянные"
# alcohol
A_SHOP = "Возможная продажа алкоголя в магазинах"
A_CATERING = "Возможная подача алкоголя в общепите"
A_SPECIALISED = "Специализированная продажа алкоголя"
A_BAR = "Подача алкоголя в барах и пабах"

TYPE_EN = {
    T_POSSIBLE: "Possible tobacco sale",
    T_SPECIALISED: "Specialised tobacco shop",
    T_VAPE: "Vape & nicotine products",
    T_HOOKAH: "Hookah lounges",
    A_SHOP: "Possible alcohol sale (shops)",
    A_CATERING: "Possible alcohol service (catering)",
    A_SPECIALISED: "Specialised alcohol shop",
    A_BAR: "Alcohol service in bars & pubs",
}
HOOKAH_TYPE = TYPE_EN[T_HOOKAH]

# The two tiers that matter for every count.  "Direct" types name the product;
# "inferred" types only say the object *could* sell it.
INFERRED_TYPES = {T_POSSIBLE, A_SHOP, A_CATERING}

# On-trade: alcohol served for consumption on the premises.  Kept apart from
# off-trade because the licence registry licenses the two separately.
ON_TRADE_TYPES = {A_CATERING, A_BAR}

RISK_ALCOHOL, RISK_TOBACCO = "Alcohol", "Tobacco"


def _load_one(path, risk: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    df["risk"] = risk
    df["type_en"] = df["type_orig"].map(TYPE_EN).fillna(df["type_orig"])
    df["evidence"] = np.where(df["type_orig"].isin(INFERRED_TYPES), "Inferred", "Direct")
    df["trade"] = np.where(df["type_orig"].isin(ON_TRADE_TYPES), "on-trade", "off-trade")
    return df


def find(directory: Path) -> tuple[Path, Path]:
    """(tobacco CSV, alcohol CSV) in a v4 delivery directory, found by their file names."""
    files = sorted(Path(directory).glob("*.csv"))
    tob = [f for f in files if "табач" in f.name.lower()]
    alc = [f for f in files if "алкогол" in f.name.lower()]
    if len(tob) != 1 or len(alc) != 1:
        raise FileNotFoundError(f"{directory}: expected one tobacco and one alcohol CSV, "
                                f"found {[f.name for f in files]}")
    return tob[0], alc[0]


def load(directory: Path) -> pd.DataFrame:
    """Both layers stacked -- tobacco first, as the archive stacked them -- with `dual`.

    Order matters: the licence join breaks ties on the lower row, so stacking
    the layers the other way round changes which of two equal claims wins.
    """
    tob, alc = find(directory)
    df = pd.concat([_load_one(tob, RISK_TOBACCO), _load_one(alc, RISK_ALCOHOL)],
                   ignore_index=True)
    df["site_key"] = (df["latitude"].round(6).astype(str) + "|"
                      + df["longitude"].round(6).astype(str))
    df["dual"] = df["site_key"].duplicated(keep=False)
    return df


def objects(v4: pd.DataFrame) -> pd.DataFrame:
    """The records as the licence join reads them: one row per record.

    `trade` is the licence trade a record needs.  A hookah lounge serves on the
    premises; the loader types every tobacco row off-trade, which would match it
    against shop licences, so it is moved to on-trade here.  `brand_learn` marks
    the layer brands are learned from: alcohol only -- a tobacco kiosk inside a
    supermarket stands at the supermarket's licence every time, which is exactly
    the co-location the vote must not mistake for ownership.
    """
    trade = np.where(v4["type_en"].astype(str).to_numpy() == HOOKAH_TYPE, "on-trade",
                     v4["trade"].astype(str).to_numpy())
    out = pd.DataFrame({
        "delivery": "v4",
        "city": v4["city_orig"].fillna("").astype(str) if "city_orig" in v4 else "",
        "name": v4["name_orig"].fillna("").astype(str),
        "address": v4["adress_orig"].fillna("").astype(str),
        "lat": pd.to_numeric(v4["latitude"], errors="coerce"),
        "lon": pd.to_numeric(v4["longitude"], errors="coerce"),
        "site_key": v4["site_key"].astype(str),
        "category": v4["risk"].astype(str),
        "generic": [is_generic(v) for v in v4["name_orig"].fillna("").astype(str)],
        "n_rows": 1,
        "trade": trade,
        "risk": v4["risk"].astype(str),
        "brand_learn": (v4["risk"].astype(str) == RISK_ALCOHOL).to_numpy(),
    })
    out.index = pd.Index([f"V{i:05d}" for i in range(len(out))], name="object_id")
    obj.validate(out)
    return out


# --------------------------------------------------------------------------
# one CSV as a delivery: ObjectTable + RowMap
# --------------------------------------------------------------------------
RISK_OF_CATEGORY = {CATEGORY_ALCOHOL: RISK_ALCOHOL, CATEGORY_TOBACCO: RISK_TOBACCO}

# Columns of the ObjectTable beyond the contract, produced here.
ADAPTER_COLUMNS = ["entity", "types", "trade", "risk", "evidence", "brand_learn", "osm", "gis",
                   "arkh_admin", "osm_urls", "gis_urls", "yandex_region", "yandex_contractor_rows"]


def read_rows(path: Path | str) -> pd.DataFrame:
    """The CSV as delivered (semicolon, UTF-8 with BOM): the deliverable's own columns."""
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def load_delivery(path: Path | str, delivery: str, yandex_region: str,
                  category: str = "") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """One v4 CSV -> (ObjectTable, RowMap, raw rows), as `final_xlsx.load` does for the XLSX.

    A row is an object: no two rows of one file share a name and a coordinate,
    and the licence join's own entity rule decides which records are one shop.
    Everything the join reads (`name`, `address`, `trade`, `risk`,
    `brand_learn`, ...) comes from `objects()`, so the join sees exactly what
    the archive's did; the row order is the file's.

    Two departures from `objects()`, neither read by the join:

    * `generic` is the *directory* rule (`core.names.is_generic_directory`):
      the final rule's extra tokens are catering words ("кофе", "шаурма"),
      and the archive judged the v4 Yandex answers with the directory rule.
    * `address` keeps the export's city prefix ("Архангельск, ..."): the join
      drops it as a home city, the Yandex query strips it.

    The provider flags are the contractor's `providers` column ("osm",
    "2gis", "2gis+osm"); `matched_source_ids` also lists ids of the third
    export a record was merged with, so it gives the record links of a
    flagged provider, not the flags.
    """
    from infracheck.core import entities
    from infracheck.core.names import is_generic_directory
    from infracheck.inputs.final_xlsx import source_links

    raw = read_rows(path)
    cats = set(raw["category_orig"].dropna().astype(str))
    if len(cats) != 1 or next(iter(cats)) not in RISK_OF_CATEGORY:
        raise ValueError(f"{Path(path).name}: expected one v4 category_orig, found {sorted(cats)}")
    risk = RISK_OF_CATEGORY[next(iter(cats))]
    v4 = _load_one(path, risk)
    v4["site_key"] = v4["latitude"].round(6).astype(str) + "|" + v4["longitude"].round(6).astype(str)
    ob = objects(v4)
    n = len(ob)
    prov = v4["providers"].fillna("").astype(str)
    links = [source_links(x) for x in v4["matched_source_ids"].fillna("").astype(str)]
    osm = prov.str.contains("osm").to_numpy()
    gis = prov.str.contains("2gis").to_numpy()
    ob = ob.assign(
        delivery=delivery, category=category,
        generic=[is_generic_directory(v) for v in ob["name"]],
        entity=np.arange(n), types=v4["type_orig"].fillna("").astype(str).to_numpy(),
        evidence=v4["evidence"].to_numpy(), osm=osm, gis=gis, arkh_admin=False,
        osm_urls=[" ".join(dict.fromkeys(o)) if f else "" for (o, _), f in zip(links, osm, strict=True)],
        gis_urls=[" ".join(dict.fromkeys(g)) if f else "" for (_, g), f in zip(links, gis, strict=True)],
        yandex_region=yandex_region, yandex_contractor_rows="[]")
    ob.index = pd.Index([entities.object_id(delivery, e) for e in range(n)], name="object_id")
    ob = ob[list(obj.COLUMNS) + ADAPTER_COLUMNS].astype(
        {c: t for c, t in obj.COLUMNS.items() if c != "category"} | {"category": "string"})
    obj.validate(ob)
    rowmap = pd.DataFrame({"object_id": ob.index.to_numpy(), "entity": np.arange(n)},
                          index=pd.RangeIndex(n, name="row"))
    return ob, rowmap, raw
