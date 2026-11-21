"""The FSRAR licence registry as a check: `licmatch.match()` -> Evidence.

Ported from the archive's `arkhinfra.final_egais` and `arkhinfra.v4_egais`
(tag `archive-2026-10-30`).  The matching rule is `packages/licmatch`; what
lives here is everything about *this* source and *these* deliveries, and only
that: the two `licmatch.Policy` configurations, the Russian sentence a reviewer
reads for each outcome, and the archive's wide output columns.

**Catering (`run_catering`, the final delivery).**  A catering licence in force
is an independent source -- the state registers it against a street address,
for a named holder, whatever OSM, 2GIS or Yandex say -- but only for the object
it actually belongs to, and the policy is deliberately stricter than the v4
join:

* **The licence must permit catering and be in force.**  A retail licence at a
  café's address says a shop sells alcohol there, not that the café exists.
* **The name must agree, always.**  Catering shares addresses -- a shopping
  centre or a hotel holds a food court's worth of venues and a handful of
  licences -- and the registry's coordinate cannot tell them apart.  An address
  alone never confirms anything.  The name may agree with the holder ("Жасмин"
  / ООО «Жасмин») or with the venue name the registry itself writes into the
  premises text of the outlet address («ресторан "Йоко"»), which is how a venue
  run by ООО «Бермуда» can still be recognised.
* **A generic fascia confirms nothing.**  "Кафе", "Столовая No 15": no name can
  agree.
* **One outlet, one venue.**  If one licensed outlet (holder INN @ address @
  venue) agrees with two objects of different brands, only the stronger keeps
  it.  Records of one venue under several spellings share it.

No brand is learned for catering.  Catering chains in these cities trade almost
entirely as franchises, each under its own local company, so a fascia -> holder
vote would learn one franchisee and wrongly extend it to the others; the manual
retail table (`licmatch.RU_RETAIL_ALIASES`) names no catering operator at all.

**The risk layers (`run_v4`, the fourth delivery).**  Which licence each v4
record holds: one outlet per shop across both layers, the licence must permit
the trade unless the name says the holder is the shop, the known brands rule
pairs out both ways, an archived licence is the object's own only if the name
agrees, and fascia -> holder pairs are learned from the alcohol layer.  Two
things about the registry govern the reading:

* **It licenses alcohol, not tobacco.**  A licence found against a tobacco row
  means the object also holds an alcohol licence -- never that the tobacco tag
  is corroborated (`licence_evidence = probable`).
* **It is a register, not a list of open outlets.**  Rows without coordinates
  are reached only by the package's strict address-only route, labelled as such.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import licmatch as lm
import numpy as np
import pandas as pd
from licmatch import stages as lms

from infracheck.core import evidence as ev
from infracheck.registers.egais import CATERING, RETAIL

SOURCE = CHECK = "egais"
LABEL = "ЕГАИС"
KIND_SCHEME = "egais_activity"

# --------------------------------------------------------------------------
# catering: the final delivery
# --------------------------------------------------------------------------
# The final delivery's city label -> the registry's settlement name.  The
# registry side carries no city in its toponym (only a settlement *inside* the
# city), so the join is blocked by city rather than scored.
CITIES = {"Архангельск": "архангельск", "Северодвинск": "северодвинск",
          "Новодвинск": "новодвинск"}

# Place words: a venue may well be called "Двина" (and ООО «Двина» run it), so
# they do not make a name generic, but a shared place word alone is not a
# shared name -- "Северное сияние" is not ООО «Север Плюс».
PLACE_WORDS = frozenset({
    "архангельск", "северодвинск", "новодвинск", "поморье", "поморский",
    "поморская", "север", "северный", "северная", "двина", "двинская",
    "двинской", "беломорский", "беломорье",
})

OUT_COLUMNS = ["egais_match", "egais_licence_no", "egais_holder", "egais_inn",
               "egais_status", "egais_activity", "egais_outlet_address",
               "egais_distance_m", "egais_score", "egais_note"]


def catering_policy() -> lm.Policy:
    """The final delivery's rule: own in-force catering licence, name always agreeing.

    The explanation ladder is the archive's, in its order: another holder's
    in-force licence at the address; the object's own-named licence lapsed; its
    own-named licence in force one building over; only lapsed licences of
    another holder; only a retail licence.
    """
    return lm.Policy(
        params=lm.DEFAULTS,
        home_cities=tuple(CITIES),
        block="city",
        kinds=lm.Kinds(allowed=frozenset({CATERING}), wrong=frozenset({RETAIL})),
        require_in_force=True,
        name_policy=lms.NAME_REQUIRED,
        name_fields=(lms.FIELD_HOLDER, lms.FIELD_VENUE),
        place_words=PLACE_WORDS,
        # The outlet is the holder at one address *and* one named venue where
        # the registry names it: ООО «Луда-Старт» files "бар Луда" and "кафе
        # Крона" at Нагорная, 3 under one licence number, and they are two venues.
        unit=("street", "house", "venue"),
        exclusive=lm.Exclusive(share=lms.SHARE_NAME, fallback=False),
        explain=(
            lm.Rung(lm.OTHER_HOLDER, (lm.C_ADDRESS, lm.C_KIND, lm.C_IN_FORCE)),
            lm.Rung(lm.INACTIVE, (lm.C_ADDRESS, lm.C_KIND, lm.C_NAME)),
            lm.Rung(lm.ELSEWHERE, (lm.C_NEAR, lm.C_KIND, lm.C_IN_FORCE, lm.C_NAME),
                    forbid=(lm.C_ADDRESS,)),
            lm.Rung(lm.OTHER_HOLDER, (lm.C_ADDRESS, lm.C_KIND)),
            lm.Rung(lm.WRONG_KIND, (lm.C_ADDRESS, lm.C_WRONG_KIND, lm.C_IN_FORCE)),
        ),
    )


_SECOND_ADDRESS = re.compile(r"/\s*(?=(?:" + "|".join(CITIES) + r")\b)")


def split_address(v: str) -> tuple[str, str]:
    """"Троицкий проспект, 17/Архангельск, набережная ..., 61" -> (first, second).

    A corner building is written as two addresses.  Only a slash followed by a
    city name starts a second address; "37/1" and "Мира, 5/3" are one house.
    """
    parts = _SECOND_ADDRESS.split(str(v), maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def _short_holder(v: str) -> str:
    """The charter's short form ('ООО "Престо"'), which is what a reader recognises."""
    s = str(v)
    i = s.lower().find(lm.SHORT_NAME_MARKER)
    return s[i + len(lm.SHORT_NAME_MARKER):].strip() if i >= 0 else s.strip()


def _fmt_date(v) -> str:
    return v.strftime("%d.%m.%Y") if pd.notna(v) else ""


def catering_note(row, reg: pd.Series | None, claimant: str) -> str:
    """The archive's `egais_note` for one result row; '' when nothing is there."""
    st, failed = row["status"], row["failed_rule"]
    if st == lm.OWN:
        via = "владелец" if row["name_via"] == lms.FIELD_HOLDER else \
            "название заведения в адресе лицензии"
        return f"подтверждено: совпадает {via}"
    if st == lm.TAKEN:
        return (f"лицензия общепита по адресу совпадает по названию, но закреплена "
                f"за объектом «{claimant}»")
    if st == lm.OTHER_HOLDER and failed == lm.C_NAME:
        return ("действующая лицензия общепита по адресу есть, но название объекта "
                "общее — владельца по нему не установить" if row["generic"] else
                f"действующая лицензия общепита по адресу есть, но другого владельца "
                f"({_short_holder(reg['org_name'])})")
    if st == lm.INACTIVE:
        when = _fmt_date(reg["status_changed"])
        return (f"лицензия общепита с тем же названием по адресу не действует "
                f"({reg['status']}{', ' + when if when else ''})")
    if st == lm.ELSEWHERE:
        return (f"действующая лицензия общепита с тем же названием в "
                f"{row['distance']:.0f} м, но по другому адресу")
    if st == lm.OTHER_HOLDER:
        return ("по адресу только недействующие лицензии общепита другого владельца "
                f"({_short_holder(reg['org_name'])})")
    if st == lm.WRONG_KIND:
        return ("по адресу только розничная лицензия с тем же названием (не общепит)"
                if lm.C_NAME not in failed.split("+") else
                "по адресу только розничная лицензия (не общепит)")
    return ""


# Evidence axes per licmatch status.
IDENTITY = {lm.OWN: "strong", lm.INACTIVE: "strong", lm.TAKEN: "weak",
            lm.OTHER_HOLDER: "weak", lm.OTHER_CHAIN: "weak", lm.WRONG_KIND: "weak",
            lm.ELSEWHERE: "weak", lm.NONE: "none"}
RECORD_STATE = {lm.OWN: "current", lm.INACTIVE: "inactive", lm.TAKEN: "current",
                lm.OTHER_HOLDER: "current", lm.OTHER_CHAIN: "current",
                lm.WRONG_KIND: "current", lm.ELSEWHERE: "current", lm.NONE: "unknown"}


def record_state(status: str, record: int, register: pd.DataFrame) -> str:
    """The cited record's own state: `inactive` whenever it is not in force.

    `record_state` describes the record, `identity` the link, so a weak row
    citing another holder's lapsed licence is still `inactive`.
    """
    if record >= 0 and not bool(register["in_force"].iat[record]):
        return "inactive"
    return RECORD_STATE[status]


def _pct(v) -> float:
    """licmatch's 0..1 name term on Evidence's 0..100 (rapidfuzz) scale."""
    return round(100.0 * float(v), 1) if pd.notna(v) and np.isfinite(v) else np.nan


def _num(v, nd: int) -> float:
    return round(float(v), nd) if pd.notna(v) and np.isfinite(v) else np.nan


def run_catering(objects: pd.DataFrame, register: pd.DataFrame,
                 policy: lm.Policy | None = None) -> pd.DataFrame:
    """Does an in-force catering licence of this object's own stand at its address?

    `objects` has columns city (the delivery's label: "Архангельск"), name,
    address (street, house -- no city prefix, may be ''), lat, lon, indexed by
    object id; `register` is `registers.egais.to_register()`'s table.  Returns
    Evidence, one row per object: `identity = strong` and `record_state =
    current` only when the licence is the object's own and in force.  The
    archive's wide columns travel along as `egais_*` (see `wide()`).
    """
    policy = policy or catering_policy()
    first, second = zip(*(split_address(v) for v in objects["address"].fillna("").astype(str)),
                        strict=True) if len(objects) else ((), ())
    ob = pd.DataFrame({
        # A city the registry extract does not cover meets no licence.
        "city": objects["city"].astype(str).str.strip().map(CITIES).fillna(""),
        "name": objects["name"].fillna("").astype(str),
        "address": list(first), "address_2": list(second),
        "lat": objects["lat"], "lon": objects["lon"]}, index=objects.index)
    res = lm.match(ob, register, policy).table
    names = ob["name"]

    rows = []
    for oid, r in res.iterrows():
        j = int(r["record"])
        reg = register.iloc[j] if j >= 0 else None
        claimant = names.get(r["claimed_by"], "") if r["claimed_by"] is not None else ""
        note = catering_note(r, reg, claimant) if j >= 0 or r["status"] == lm.TAKEN else ""
        own = bool(r["assigned"])
        row = {"object_id": str(oid), "check": CHECK, "source": SOURCE, "label": LABEL,
               "identity": IDENTITY[r["status"]], "record_state": record_state(r["status"], j, register),
               "kind_scheme": KIND_SCHEME if j >= 0 else "",
               "egais_match": own, "egais_status_code": r["status"],
               "egais_failed_rule": r["failed_rule"], "egais_note": note}
        if reg is not None:
            holder = _short_holder(reg["org_name"])
            row.update({
                "kind_text": reg["kind"], "matched_name": holder,
                "matched_address": reg["outlet_address"],
                "distance_m": _num(r["distance"], 1), "name_score": _pct(r["name"]),
                "ref": reg["licence_no"],
                "egais_outlet_key": json.dumps([str(reg["licence_no"]), str(reg["outlet_address"])],
                                               ensure_ascii=False),
                "egais_licence_no": reg["licence_no"], "egais_holder": holder,
                "egais_inn": reg["holder_id"], "egais_status": reg["status"],
                "egais_activity": reg["activity"], "egais_outlet_address": reg["outlet_address"],
                "egais_distance_m": _num(r["distance"], 1), "egais_score": _num(r["score"], 3),
            })
        # Counted: the licence itself (final_confirm.decide); otherwise the note,
        # which the category prefixes with the label ("ЕГАИС: …").
        row["text"] = (f"лицензия {row['egais_licence_no']} ({row['egais_status']}), "
                       f"{row['egais_holder']}, {row['egais_outlet_address']}") if own else note
        rows.append(row)
    out = ev.make(rows)
    if "egais_outlet_key" not in out:
        out["egais_outlet_key"] = ""
    for c in OUT_COLUMNS[1:]:
        if c not in out:
            out[c] = np.nan if c in ("egais_distance_m", "egais_score") else ""
    return out


def _wide_catering(evidence: pd.DataFrame) -> pd.DataFrame:
    """The archive's `final_egais.csv` columns, one row per object, from `run_catering`."""
    e = evidence[evidence["check"] == CHECK].set_index("object_id")
    out = pd.DataFrame(index=e.index)
    out["egais_match"] = e["egais_match"].astype(bool)
    for c in OUT_COLUMNS[1:]:
        if c in ("egais_distance_m", "egais_score"):
            out[c] = pd.to_numeric(e[c], errors="coerce")
        else:
            out[c] = e[c].fillna("").astype(str)
    return out[OUT_COLUMNS]


# --------------------------------------------------------------------------
# the risk layers: the fourth delivery
# --------------------------------------------------------------------------
# Fascia spellings that name one brand.  `brand_key()` folds only the "&";
# these are the export's own variants of one fascia, found in the data, which
# no general rule reaches: a stylised "Петр&скй" beside "Петровский", three
# spellings of Алко Shop, a chain's sub-format ("Пятёрочка Кафе с собой",
# "Магнит Опт").  Applied to the brand key only; the name term is untouched.
FASCIA_SPELLINGS = {
    "петр скй": "петровский",
    "алко shop": "алкоshop",
    "алко shoo": "алкоshop",
    "винлаб напитков": "винлаб",
    "пятерочка собой": "пятерочка",
    "магнит-опт": "магнит",
    "магнит моя цена": "магнит",
}

# Decisions on learned pairs that the counts alone cannot make, keyed by
# (brand key, INN).  "reject" drops an accepted pair, "accept" admits one the
# thresholds refused; the reason is carried into the brand table.  Every entry
# must say why -- this is the one place a learned pair is overruled by hand.
BRAND_REVIEW: dict[tuple[str, str], tuple[str, str]] = {}

# A name comparison this strong says the holder *is* the shop ("Луда" /
# ООО «Луда-Старт», 0.90) -- enough to let a pair past the trade rule, and to
# let a licence of a known chain go to an object that names that chain
# without matching its fascia key.
STRONG_NAME = 0.80
# The least name agreement at which an archived licence may be quoted as an
# object's own when an in-force licence of its trade stands at the address:
# below it the archived one is the premises' previous tenant (Пятёрочка quoting
# ООО «Южанка», 2014), not the object's.
ARCHIVED_NAME_MIN = 0.60

# Which licence activity permits what an object is tagged with.  An off-trade
# object needs a retail licence, an on-trade one a catering licence ("при
# оказании услуг общественного питания"); wholesale, production and transport
# licences permit neither.
TRADE_ACTIVITY = {"off-trade": frozenset({RETAIL}), "on-trade": frozenset({CATERING})}

# The tallies, named as they appear in the output.
GROUP_MATCHED, GROUP_ACTIVE, GROUP_ACTIVE_RETAIL = "matched", "active", "active_retail"


def v4_policy(learn: bool = True, exclusive: bool = True, brand_rule: bool = True,
              params: lm.MatchParams = lm.DEFAULTS) -> lm.Policy:
    """The v4 join's rule; the three flags are the archive's --no-learn /
    --no-exclusive / --no-brand-rule, all on by default."""
    return lm.Policy(
        params=params,
        home_cities=("Архангельск",),
        kinds=lm.Kinds(column="trade", by_value=TRADE_ACTIVITY, holder_override=STRONG_NAME),
        name_policy=lms.NAME_TIEBREAK,
        name_fields=(lms.FIELD_HOLDER,),
        brands=lm.Brands(mode=lms.BRANDS_LEARNED if learn else lms.BRANDS_KNOWN,
                         conflict=brand_rule, spellings=FASCIA_SPELLINGS, review=BRAND_REVIEW,
                         learn_column="brand_learn", conflict_name=STRONG_NAME),
        archived=lm.Archived(name_min=ARCHIVED_NAME_MIN),
        # A licence of the object's own trade first, then one in force.
        prefer=(lm.C_KIND, lm.C_IN_FORCE),
        unit=(lms.UNIT_TOPONYM,),
        # Both risk layers compete for one outlet together; records of one shop share.
        # The archive named the last holder of a shared outlet as the rival.
        exclusive=lm.Exclusive(share=lms.SHARE_ENTITY, fallback=True, rival=lms.RIVAL_LAST)
        if exclusive else None,
        entities=lm.Entities(max_m=50.0, name_min=80.0, same_name_m=5.0),
        explain=(lm.Rung(lm.OTHER_CHAIN, (lm.C_ADDRESS, lm.C_KIND)),
                 lm.Rung(lm.WRONG_KIND, (lm.C_ADDRESS,))),
        tallies={GROUP_MATCHED: lm.Tally(),
                 GROUP_ACTIVE: lm.Tally(in_force=True),
                 GROUP_ACTIVE_RETAIL: lm.Tally(in_force=True, kinds=frozenset({RETAIL, CATERING}))},
    )


# `licence_status` describes the licence *this object* was given, never merely
# what the address holds -- the per-address tallies (`licence_n_*`) do that.
STATUS_ACTIVE = "Active"          # its own licence, in force
STATUS_ARCHIVED = "Archived"      # its own licence, expired / terminated / annulled
STATUS_TAKEN = "Taken"            # its outlet went to another shop with the stronger claim
STATUS_OTHER_TRADE = "Other trade"  # only licences of the other trade at the address
STATUS_OTHER_CHAIN = "Other chain"  # each licence of its trade belongs to another known chain
# Never the bare string "None": pandas reads that back as NaN.
STATUS_NONE = "Not in registry"
V4_STATUS = {lm.OWN: STATUS_ACTIVE, lm.INACTIVE: STATUS_ARCHIVED, lm.TAKEN: STATUS_TAKEN,
             lm.OTHER_CHAIN: STATUS_OTHER_CHAIN, lm.WRONG_KIND: STATUS_OTHER_TRADE,
             lm.NONE: STATUS_NONE}

# What a matched licence is evidence *of*.  For an alcohol row it is the
# licence for the very product the row is tagged with.  For a tobacco row there
# is no tobacco registry at all: the licence says the address legally sells
# alcohol, and Russian retail sells alcohol and tobacco through the same
# counters -- a probability, not a confirmation, stated rather than scored.
EVIDENCE_DIRECT, EVIDENCE_PROBABLE, EVIDENCE_WEAK = "direct", "probable", "weak"
EVIDENCE_NOTE = {
    EVIDENCE_DIRECT:
        "Прямое подтверждение: ЕГАИС лицензирует именно алкоголь — тот "
        "продукт, которым размечен объект. Статус лицензии см. в licence_status.",
    EVIDENCE_PROBABLE:
        "Вероятное подтверждение (косвенное): табачного реестра в ЕГАИС не "
        "существует. Объект сам держит действующую розничную алкогольную "
        "лицензию, а алкоголь и табак в рознице продаются через один и тот же прилавок, "
        "поэтому лицензия косвенно поддерживает табачную метку, но не "
        "подтверждает продажу табака.",
    EVIDENCE_WEAK:
        "Слабое подтверждение: собственная лицензия объекта архивная или "
        "нерозничная (действующие лицензии по адресу, если есть, закреплены за "
        "другими объектами), поэтому даже косвенный довод в пользу продажи "
        "табака здесь не работает.",
    "": "",
}
DIRECT_RISK = "Alcohol"

LICENCE_FIELDS = {
    "licence_no": "licence_no",
    "licence_prev_no": "licence_prev_no",
    "licence_status_raw": "status_en",
    "licence_status_registry": "status",
    "licence_status_reason": "status_reason",
    "licence_date_issued": "date_issued",
    "licence_date_expires": "date_expires",
    "licence_status_changed": "status_changed",
    "licence_activity": "activity",
    "licence_activity_class": "activity_class",
    "licence_products": "products",
    "licence_org_name": "org_name",
    "licence_org_inn": "org_inn",
    "licence_outlet_address": "outlet_address",
    "licence_issuing_body": "issuing_body",
}
_DATE_COLS = ("licence_date_issued", "licence_date_expires", "licence_status_changed")

MATCH_FIELDS = ["licence_status", "licence_evidence", "licence_evidence_note",
                "licence_match_method", "licence_match_level",
                "licence_score", "licence_addr_score", "licence_name_score",
                "licence_name_basis", "licence_claimed_by", "licence_claim_margin",
                "licence_trade_match", "licence_outlet_id",
                "licence_distance_m", "licence_n_matched", "licence_n_active",
                "licence_n_active_retail", "licence_all_nos", "licence_active_nos"]

LEVEL_HOUSE = "house"     # both sides name the same building
LEVEL_STREET = "street"   # same street, one side writes no house number
LEVEL_HOUSE_MIN = 0.85    # the house term at which "same building" is claimed


@dataclass(frozen=True)
class V4Result:
    table: pd.DataFrame          # the v4 records + MATCH_FIELDS + LICENCE_FIELDS
    evidence: pd.DataFrame       # one Evidence row per record
    brands: lm.BrandResult | None
    match: lm.MatchResult


# How the name agreed, for the reader of «Основания».
BASIS_RU = {lms.BASIS_MANUAL: "известная сеть", lms.BASIS_LEARNED: "выученная пара вывеска → ИНН",
            lms.BASIS_FUZZY: "совпадает название", "venue": "совпадает название заведения"}


def name_agrees(ob: pd.DataFrame, register: pd.DataFrame, table: pd.DataFrame,
                policy: lm.Policy, brands: lm.BrandResult | None, *, include_venue: bool = True) -> np.ndarray:
    """Does the object's own licence agree with its name?  One bool per object.

    The licence is the object's by the join's rules, but the join (`tiebreak`)
    admits a licence on the address alone.  Its *identity* is strong only when
    the name says the holder is this shop, as strictly as in catering:

    * `manual` -- the holder is a known chain of the fascia (alias table);
    * `learned` -- a learned fascia -> INN pair, and only one that survives
      leave-one-out (`loo_accepted`) or a person accepted in the review: a pair
      resting on all its voting addresses would confirm itself there;
    * `fuzzy` -- the names compared as strings agree by `licmatch.names_agree`,
      the word-level test the catering join requires;
    * the venue in the premises text may also agree, without altering the join;
    * `generic` -- never: «Продукты» names no holder.

    Rows without an own licence are False.
    """
    rec = table["record"].to_numpy(int)
    hit = table["assigned"].to_numpy(bool)
    basis = table["name_basis"].fillna("").astype(str).to_numpy()
    aliases, sp = policy.aliases, policy.brands.spellings
    good = set()
    if brands is not None:
        bt = brands.table
        ok = bt["used"] & (bt["loo_accepted"] | (bt["review"] == lms.REVIEW_ACCEPT))
        good = set(zip(bt.loc[ok, "fascia"], bt.loc[ok, "holder"], strict=True))
    holders = register["holder_name"].astype(str).to_numpy()
    venues = register["venue_name"].fillna("").astype(str).to_numpy()
    inns = register["holder_id"].astype(str).to_numpy()
    out = np.zeros(len(ob), dtype=bool)
    for i, name in enumerate(ob["name"].fillna("").astype(str)):
        if not hit[i]:
            continue
        b = basis[i]
        if b == lms.BASIS_MANUAL:
            out[i] = True
        elif b == lms.BASIS_LEARNED:
            key = lm.brand_key(lm.fascia_name(name, aliases=aliases))
            out[i] = (sp.get(key, key), inns[rec[i]]) in good
        elif b == lms.BASIS_FUZZY:
            out[i] = lm.names_agree(lm.fascia_name(name, aliases=aliases),
                                    lm.holder_name(holders[rec[i]], aliases=aliases), policy.place_words)
        if include_venue and not out[i] and b != lms.BASIS_GENERIC:
            out[i] = lm.names_agree(lm.fascia_name(name, aliases=aliases),
                                    lm.fascia_name(venues[rec[i]], aliases=aliases), policy.place_words)
    return out


def _v4_text(st: str, risk: str, agrees: bool, basis: str, reg: pd.Series | None,
             evidence_kind: str, claimed_by: str) -> str:
    """The Russian clause of one v4 row: counted (own, name agrees) or after « || ».

    A tobacco row speaks only when the object holds an alcohol licence of its
    own, and says what that is *not*: the registry has no tobacco class, so the
    licence never verifies a tobacco tag (`licence_evidence` probable / weak).
    """
    lic = (f"лицензия {reg['licence_no']} ({reg['status']}), {_short_holder(reg['org_name'])}, "
           f"{reg['outlet_address']}") if reg is not None else ""
    if risk != DIRECT_RISK:
        if evidence_kind == EVIDENCE_PROBABLE:
            return (f"у объекта своя действующая розничная алкогольная {lic} — косвенный довод: "
                    "табачного реестра нет, продажу табака лицензия не подтверждает")
        if evidence_kind == EVIDENCE_WEAK:
            return f"своя алкогольная {lic} архивная или нерозничная — довода о табаке нет"
        return ""
    if st == lm.OWN:
        if agrees:
            return f"{lic}; имя: {BASIS_RU.get(basis, basis)}"
        why = ("вывеска общая — владельца по ней не установить" if basis == lms.BASIS_GENERIC
               else "имя владельца или заведения с вывеской не сходится")
        return f"{lic} — закреплена по адресу, но {why}"
    if st == lm.INACTIVE:
        when = _fmt_date(reg["status_changed"]) if reg is not None else ""
        return f"собственная {lic} не действует{' с ' + when if when else ''}"
    if st == lm.TAKEN:
        return f"лицензия по адресу закреплена за объектом «{claimed_by}»"
    if st == lm.OTHER_CHAIN:
        return "по адресу только лицензии другой сети"
    if st == lm.WRONG_KIND:
        return "по адресу только лицензии другой торговли (розница ↔ общепит)"
    return "в реестре по адресу лицензий нет"


def match_v4(ob: pd.DataFrame, register: pd.DataFrame, policy: lm.Policy | None = None
             ) -> tuple[pd.DataFrame, pd.DataFrame, lm.MatchResult]:
    """The v4 join over an ObjectTable: (fields, Evidence, MatchResult).

    `ob` holds both risk layers together -- tobacco first, as the archive
    stacked them, since ties break on the lower row -- with columns name,
    address, lat, lon, city, `trade`, `risk` and `brand_learn`
    (`inputs.v4_csv`).  `fields` is indexed like `ob` and carries the
    archive's `MATCH_FIELDS` + `LICENCE_FIELDS`; the Evidence rows carry them
    too, plus `licence_name_agrees`.
    """
    policy = policy or v4_policy()
    mr = lm.match(ob, register, policy)
    t = mr.table
    hit = t["assigned"].to_numpy(bool)
    rec = t["record"].to_numpy(int)
    pick = np.where(rec >= 0, rec, 0)
    active = register["in_force"].to_numpy(bool)
    retail = register["is_retail"].to_numpy(bool)
    own_active_retail = hit & active[pick] & retail[pick]

    def gather(col, dtype=np.float32):
        return np.where(hit, t[col].to_numpy(dtype), np.nan)

    out = pd.DataFrame(index=ob.index)
    out["licence_status"] = t["status"].map(V4_STATUS).to_numpy()
    out["licence_match_method"] = np.where(hit, t["method"].to_numpy(object), "")
    house = gather("house")
    out["licence_match_level"] = np.where(
        hit, np.where(house >= LEVEL_HOUSE_MIN, LEVEL_HOUSE, LEVEL_STREET), "")
    out["licence_score"] = gather("score").round(3)
    out["licence_addr_score"] = gather("addr").round(3)
    basis = np.where(hit, t["name_basis"].to_numpy(object), "")
    # A generic fascia's name was never scored; a 0 there would read as
    # "the names disagree", which nobody claimed.
    out["licence_name_score"] = np.where(basis == lms.BASIS_GENERIC, np.nan, gather("name").round(3))
    out["licence_name_basis"] = basis
    out["licence_distance_m"] = gather("distance").round(1)
    out["licence_trade_match"] = np.where(hit, np.where(t["kind_match"].to_numpy(bool), "yes", "no"), "")
    out["licence_outlet_id"] = np.where(hit, t["unit"].to_numpy(object), "")
    for g in (GROUP_MATCHED, GROUP_ACTIVE, GROUP_ACTIVE_RETAIL):
        out[f"licence_n_{g}"] = t[f"n_{g}"].to_numpy(int)

    names = ob["name"].fillna("").astype(str).to_numpy()
    addrs = ob["address"].fillna("").astype(str).to_numpy()
    pos = {k: i for i, k in enumerate(ob.index)}
    taken = (t["status"] == lm.TAKEN).to_numpy()
    out["licence_claimed_by"] = [f"{names[pos[c]]} ({addrs[pos[c]]})" if tk and c is not None else ""
                                 for tk, c in zip(taken, t["claimed_by"], strict=True)]
    if (taken & t["claimed_by"].isna().to_numpy()).any():
        raise AssertionError("a Taken row with no rival -- the assignment is inconsistent")
    out["licence_claim_margin"] = np.round(t["margin"].to_numpy(float), 3)

    risk = ob["risk"].astype(str).to_numpy()
    kind = np.where(risk == DIRECT_RISK, EVIDENCE_DIRECT,
                    np.where(own_active_retail, EVIDENCE_PROBABLE, EVIDENCE_WEAK))
    out["licence_evidence"] = np.where(hit, kind, "")
    out["licence_evidence_note"] = pd.Series(out["licence_evidence"]).map(EVIDENCE_NOTE).to_numpy()
    out["licence_all_nos"] = t[f"ids_{GROUP_MATCHED}"].to_numpy(object)
    out["licence_active_nos"] = t[f"ids_{GROUP_ACTIVE}"].to_numpy(object)
    for col, src in LICENCE_FIELDS.items():
        vals = register[src].to_numpy(dtype=object)
        out[col] = np.where(hit, vals[pick], "")
    for col in _DATE_COLS:
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    out = out[MATCH_FIELDS + list(LICENCE_FIELDS)]
    # The terms are float32 (licmatch's matrices); widened as they are they
    # print as 0.12300000339746475.  Their float32 decimal form is the number
    # the archive wrote, so that is the float64 carried on.
    for c in out.columns:
        if out[c].dtype == np.float32:
            out[c] = pd.to_numeric(out[c].astype(str))

    agrees = name_agrees(ob, register, t, policy, mr.brand)
    holder_agrees = name_agrees(ob, register, t, policy, mr.brand, include_venue=False)
    agrees = np.where(risk == DIRECT_RISK, agrees, holder_agrees)
    rows = []
    for i, (oid, r) in enumerate(t.iterrows()):
        st = r["status"]
        reg = register.iloc[rec[i]] if hit[i] else None
        ident = IDENTITY[st]
        if st in (lm.OWN, lm.INACTIVE) and not agrees[i]:
            ident = "weak"
        row = {"object_id": str(oid), "check": CHECK, "source": SOURCE, "label": LABEL,
               "identity": ident, "record_state": record_state(st, int(rec[i]), register),
               "egais_status_code": st, "licence_name_agrees": bool(agrees[i]),
               # The join's shop: records within 50 m naming one brand (licmatch `entity`).
               "egais_entity": r["entity"],
               **out.iloc[i].to_dict(),
               "text": _v4_text(st, risk[i], bool(agrees[i]),
                                "venue" if agrees[i] and not holder_agrees[i] else basis[i], reg,
                                out["licence_evidence"].iat[i], out["licence_claimed_by"].iat[i])}
        if reg is not None:
            row.update({"kind_text": reg["kind"], "kind_scheme": KIND_SCHEME,
                        "matched_name": _short_holder(reg["org_name"]),
                        "matched_address": reg["outlet_address"],
                        "distance_m": _num(r["distance"], 1), "name_score": _pct(r["name"]),
                        "ref": reg["licence_no"]})
        rows.append(row)
    return out, ev.make(rows), mr


def run_v4(v4_records: pd.DataFrame, register: pd.DataFrame,
           policy: lm.Policy | None = None) -> V4Result:
    """Which licence each v4 record holds -- the archive's `v4_licences.csv` columns.

    `v4_records` is `inputs.v4_csv.load()`'s frame (both layers stacked);
    `register` is `registers.egais.to_register()` of the Arkhangelsk extract.
    `licence_status` and the licence columns describe the licence *this record*
    holds; `licence_n_*` / `licence_all_nos` describe the whole address.
    """
    from infracheck.inputs.v4_csv import objects as v4_objects

    fields, evidence, mr = match_v4(v4_objects(v4_records), register, policy)
    out = v4_records.reset_index(drop=True).copy()
    for c in fields.columns:
        out[c] = fields[c].to_numpy()
    return V4Result(table=out[list(v4_records.columns) + MATCH_FIELDS + list(LICENCE_FIELDS)],
                    evidence=evidence, brands=mr.brand, match=mr)


# --------------------------------------------------------------------------
# the check interface: Policy, run, wide
# --------------------------------------------------------------------------
MODE_CATERING, MODE_V4 = "catering", "v4"


@dataclass(frozen=True)
class Policy:
    """The registry join as configured: the mode's rule, its thresholds from config.

    `mode` picks the rule -- `catering` (`catering_policy()`: own in-force
    catering licence, the name always agreeing) or `v4` (`v4_policy()`: the
    risk layers' join, both layers exclusive together).  The structural parts
    of each (kinds, the explanation ladder, exclusivity, entities) are code.
    `params` overrides fields of `licmatch.MatchParams`; the switches below,
    left None, keep the mode's own value.  `learn` / `exclusive` /
    `brand_rule` are the v4 rule's flags (the archive's --no-learn,
    --no-exclusive, --no-brand-rule).
    """

    mode: str = MODE_CATERING
    params: Mapping = field(default_factory=dict)
    require_in_force: bool | None = None
    name_policy: str | None = None
    name_fields: tuple | None = None
    unit: tuple | None = None
    home_cities: tuple | None = None
    learn: bool = True
    exclusive: bool = True
    brand_rule: bool = True

    def licmatch(self) -> lm.Policy:
        """The `licmatch.Policy` this configuration stands for."""
        if self.mode == MODE_CATERING:
            base = catering_policy()
        elif self.mode == MODE_V4:
            base = v4_policy(learn=self.learn, exclusive=self.exclusive, brand_rule=self.brand_rule)
        else:
            raise ValueError(f"egais mode {self.mode!r}: catering or v4")
        over = {k: (tuple(v) if isinstance(v, list) else v)
                for k in ("require_in_force", "name_policy", "name_fields", "unit", "home_cities")
                if (v := getattr(self, k)) is not None}
        return replace(base, params=replace(base.params, **dict(self.params)), **over)


DEFAULT = Policy()


def run(objects: pd.DataFrame, register: pd.DataFrame, policy: Policy = DEFAULT) -> pd.DataFrame:
    """Evidence of the registry for every object, by the policy's mode.

    For `v4` the objects of every delivery that compete for one outlet (both
    risk layers) must be passed together: exclusivity is decided over the
    whole call.
    """
    if policy.mode == MODE_V4:
        return match_v4(objects, register, policy.licmatch())[1]
    return run_catering(objects, register, policy.licmatch())


def wide(evidence: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """The archive's columns, one row per object (reindexed to `index` if given).

    Catering rows give `final_egais.csv`'s `egais_*`; v4 rows give
    `v4_licences.csv`'s `MATCH_FIELDS` + `LICENCE_FIELDS` and `licence_name_agrees`.
    """
    e = evidence[evidence["check"] == CHECK]
    if "licence_status" in e.columns:
        w = e.set_index("object_id")[MATCH_FIELDS + list(LICENCE_FIELDS) + ["licence_name_agrees"]].copy()
        if index is not None:
            w = w.reindex(index)
            w["licence_name_agrees"] = w["licence_name_agrees"].fillna(False).astype(bool)
        return w
    w = _wide_catering(evidence)
    if index is not None:
        w = w.reindex(index)
        w["egais_match"] = w["egais_match"].fillna(False).astype(bool)
    return w


__all__ = ["Policy", "run", "wide", "catering_policy", "v4_policy", "run_catering", "run_v4",
           "match_v4", "name_agrees", "V4Result",
           "split_address", "catering_note", "OUT_COLUMNS", "MATCH_FIELDS", "LICENCE_FIELDS",
           "CITIES", "PLACE_WORDS", "FASCIA_SPELLINGS", "BRAND_REVIEW"]
