"""`licmatch.match()` and its five stages: an object table and a register -> a verdict per object.

The package's lower layers answer narrow questions: how alike two addresses
are (`toponym`), what a pair scores (`scoring`), which counterpart an object
quotes (`select`), which company trades under which fascia (`brands`).  A study
that uses them has to wire them together, and two studies wired them two ways:
a licence join for a risk layer (one licence per shop, the name a tie-breaker,
chains learned from the data) and a confirmation check for catering (the name
must always agree, one outlet per venue, a reason for every miss).  Both are
the same five stages with different settings, and this module is those stages:

1. **candidates** (`prepare`) -- both tables parsed into the canonical sides,
   once per address variant of an object and once per register name field.
   Nothing is blocked away: every pair is scored, so no candidate is ever
   silently withheld (`Policy.block` splits the run, it does not prune pairs).
2. **scoring** (`score`) -- the three-term score of every pair, with learned
   brands where the policy asks for them; per pair the better name field and
   the better address variant are kept.
3. **admission** (`admit`) -- a list of *named* conditions, each a boolean
   (objects x records) mask: `address` (both floors), `kind` / `kind_or_holder`,
   `in_force`, `name`, `brand`, `archived`.  A pair is admitted when it passes
   every condition the policy lists (`Policy.admission()`); the masks are kept,
   because the explanation is written in their names.
4. **assignment** (`assign`) -- one register unit (an outlet, however many rows
   the register filed it under) per object, greedy, strongest claim first.
   Objects that are one shop (`share="entity"`) or one venue by name
   (`share="name"`) share a unit; a loser either falls back to its next free
   unit (`fallback=True`) or simply loses it.
5. **explanation** (`explain`) -- for an object left without a record, the best
   rejected candidate from a ladder of near-miss rungs (`Policy.explain`), and
   the admission conditions that candidate failed (`failed_rule`).

The result is codes and numbers only (`MatchResult.table`): statuses, the cited
record's position, its terms, who claimed the unit, the address tallies.  What
a status *means* for a study -- the sentence a reviewer reads, the column names
of an output file -- is the adapter's business, not this module's.

There is no mode switch.  A behaviour enters as a `Policy` field, and a stage
reads the field, never the name of the study that set it.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from .agree import agree_matrix, is_generic_name, names_agree
from .brands import BRAND_DEFAULTS, BrandParams, BrandResult, learn_brands
from .geo import haversine_m
from .names import RU_RETAIL_ALIASES, brand_key, fascia_name, holder_name, is_generic
from .scoring import (
    DEFAULTS,
    NAME_ALIAS,
    NAME_KINDS,
    ROUTE_GEO,
    ROUTE_NAMES,
    MatchParams,
    Scores,
    accepted,
    score_matrix,
    side,
)
from .select import PREFER_LIFT
from .text import fold
from .toponym import home_city_re, parse_address_fields, parse_address_line

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------
# Statuses: what the object's own record is, or why it has none.
OWN = "own"                    # its own record, in force
INACTIVE = "inactive"          # its own record (held, or agreeing by name), not in force
TAKEN = "taken"                # an admitted unit, but another object's stronger claim holds it
OTHER_HOLDER = "other_holder"  # a record of the right kind at the address, another holder's
OTHER_CHAIN = "other_chain"    # admitted by address and kind, ruled out by the known brands
WRONG_KIND = "wrong_kind"      # at the address, but its kind does not permit the object's
ELSEWHERE = "elsewhere"        # a record that would be its own, at another address nearby
NONE = "none"                  # nothing in the register worth naming
STATUSES = (OWN, INACTIVE, TAKEN, OTHER_HOLDER, OTHER_CHAIN, WRONG_KIND, ELSEWHERE, NONE)

# Named pair conditions -- the masks `admit()` builds.
C_ADDRESS = "address"                # both floors (`scoring.accepted`)
C_NEAR = "near"                      # within max_m by coordinate (the geo route)
C_KIND = "kind"                      # the record's kind permits the object's
C_KIND_OR_HOLDER = "kind_or_holder"  # ... or the name says the holder *is* the object
C_IN_FORCE = "in_force"
C_NAME = "name"                      # the names agree (`agree.names_agree`)
C_BRAND = "brand"                    # the known brands do not rule the pair out
C_ARCHIVED = "archived"              # an archived record may be the object's own
C_WRONG_KIND = "wrong_kind"          # the record is of a kind worth naming as the wrong one
C_EXCLUSIVE = "exclusive"            # the failed "condition" of a taken object

NAME_REQUIRED, NAME_TIEBREAK = "required", "tiebreak"
FIELD_HOLDER, FIELD_VENUE = "holder", "venue"
BRANDS_OFF, BRANDS_KNOWN, BRANDS_LEARNED = "off", "known", "learned"
SHARE_ENTITY, SHARE_NAME = "entity", "name"
RIVAL_CLAIMANT, RIVAL_LAST = "claimant", "last"

# Where the chosen pair's name term came from -- `name_basis`.
BASIS_MANUAL = "manual"      # the hand-kept operator -> fascia table (`Policy.aliases`)
BASIS_LEARNED = "learned"    # a pair learned from the data
BASIS_FUZZY = "fuzzy"        # the two names compared as strings
BASIS_GENERIC = "generic"    # a generic fascia; the name term is not scored

# Unit parts (`Policy.unit`): the outlet key is holder_id + "@" + the parts.
UNIT_TOPONYM = "toponym"     # parsed settlement|street|house|suffix, raw address if unparsed
UNIT_PARTS = (UNIT_TOPONYM, "street", "house", "venue", "address_line")


# --------------------------------------------------------------------------
# the policy
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Kinds:
    """Which register kinds permit what an object is.

    Either one set for every object (`allowed`), or a set per value of an
    object column (`column` + `by_value`; a value missing from the map permits
    nothing).  `allowed=None` without a column permits every kind.

    `holder_override`: a name agreement this strong (or a known brand pair)
    lets a pair past the kind rule -- the holder *is* the shop ("Луда" / ООО
    «Луда-Старт», 0.90).  The strict `kind` mask is kept beside the relaxed
    `kind_or_holder` one, for preference and explanation.
    `wrong`: kinds the explanation names when only they stand at the address.
    """
    allowed: frozenset | None = None
    column: str | None = None
    by_value: Mapping[str, frozenset] = field(default_factory=dict)
    holder_override: float | None = None
    wrong: frozenset = frozenset()


@dataclass(frozen=True)
class Brands:
    """Known chains: whose licence a fascia may hold, and the name term's aliases.

    `mode`: `off` -- no brand knowledge at all; `known` -- the hand-kept table
    (`Policy.aliases`) only; `learned` -- that table plus (fascia, holder) pairs
    learned from the objects selected by `learn_column` (a boolean column; all
    objects when None).  `conflict` turns the both-ways rule on (`C_BRAND`).
    `spellings` folds export variants of one fascia onto its brand key;
    `review` is the manual decision on learned pairs, keyed (brand key,
    holder_id) -> ("accept" | "reject", reason).
    """
    mode: str = BRANDS_OFF
    conflict: bool = False
    spellings: Mapping[str, str] = field(default_factory=dict)
    review: Mapping[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    learn_column: str | None = None
    params: BrandParams = BRAND_DEFAULTS
    # A licence of a known chain goes to an object outside the chain only when
    # the plain name comparison reaches this.
    conflict_name: float = 0.80


@dataclass(frozen=True)
class Archived:
    """An archived record is the object's own only if the name agrees.

    ...where an in-force record of its kind stands at the address.  Below
    `name_min` (or without a known-holder or alias pair) the archived record is
    the premises' previous tenant, not the object's.
    """
    name_min: float = 0.60


@dataclass(frozen=True)
class Entities:
    """Records of one shop: within `max_m` naming the same brand (same brand key,
    or token-set similarity >= `name_min` on non-generic keys), or within
    `same_name_m` under an identical name, generic or not."""
    max_m: float = 50.0
    name_min: float = 80.0
    same_name_m: float = 5.0


@dataclass(frozen=True)
class Exclusive:
    """One unit, one object.

    `share`: who may hold a unit beside its first claimant -- records of one
    entity (`entity`, needs `Policy.entities`) or objects whose names agree
    with the claimant's (`name`).  `fallback`: a loser takes its next free
    unit; without it, it loses.  `scope`: an object column within whose groups
    exclusivity holds (None: every object competes with every other).

    `rival`: whom a taken object names as holding its best unit when several
    objects share it -- `claimant`, the first (strongest) claim, or `last`,
    the last holder in table order.  `last` is how the archive's v4 join wrote
    `licence_claimed_by` (a dict built over the holders keeps the last); it
    names a record of the claimant's own shop, and on the v4 golden data it
    differs from `claimant` on 3 of 1,694 rows.  Kept as a field so that both
    archived behaviours stay reproducible.
    """
    share: str = SHARE_ENTITY
    fallback: bool = True
    scope: str | None = None
    rival: str = RIVAL_CLAIMANT


@dataclass(frozen=True)
class Rung:
    """One near-miss query: the best candidate passing every `require` condition
    and none of the `forbid` ones gets `status`."""
    status: str
    require: tuple[str, ...]
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tally:
    """A count of distinct record ids among the admitted-by-address records."""
    in_force: bool | None = None
    kinds: frozenset | None = None


@dataclass(frozen=True)
class Policy:
    """Everything that makes one study's match differ from another's."""
    params: MatchParams = DEFAULTS
    # The city both sides share in every address; dropped, never scored.
    home_cities: tuple[str, ...] = ()
    # A column on both tables: objects meet only records of their own value.
    # An empty value meets nothing.
    block: str | None = None
    kinds: Kinds = field(default_factory=Kinds)
    require_in_force: bool = False
    name_policy: str = NAME_TIEBREAK
    name_fields: tuple[str, ...] = (FIELD_HOLDER,)
    place_words: frozenset = frozenset()
    # The operator -> fascia table applied to both sides' name keys.
    aliases: Mapping[str, str] = field(default_factory=lambda: RU_RETAIL_ALIASES)
    brands: Brands = field(default_factory=Brands)
    archived: Archived | None = None
    # Admitted pairs carrying these conditions outrank the others, first
    # highest (`select.PREFER_LIFT`).
    prefer: tuple[str, ...] = ()
    unit: tuple[str, ...] = (UNIT_TOPONYM,)
    exclusive: Exclusive | None = None
    entities: Entities | None = None
    explain: tuple[Rung, ...] = ()
    tallies: Mapping[str, Tally] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name_policy not in (NAME_REQUIRED, NAME_TIEBREAK):
            raise ValueError(f"name_policy must be required or tiebreak, got {self.name_policy!r}")
        if not self.name_fields or set(self.name_fields) - {FIELD_HOLDER, FIELD_VENUE}:
            raise ValueError(f"name_fields must be drawn from holder/venue, got {self.name_fields}")
        if self.brands.mode not in (BRANDS_OFF, BRANDS_KNOWN, BRANDS_LEARNED):
            raise ValueError(f"brands.mode {self.brands.mode!r}")
        if set(self.unit) - set(UNIT_PARTS):
            raise ValueError(f"unit parts must be drawn from {UNIT_PARTS}")
        if self.exclusive and self.exclusive.share == SHARE_ENTITY and self.entities is None:
            raise ValueError("share='entity' needs Policy.entities")
        if self.exclusive and self.exclusive.share not in (SHARE_ENTITY, SHARE_NAME):
            raise ValueError(f"exclusive.share {self.exclusive.share!r}")
        if self.exclusive and self.exclusive.rival not in (RIVAL_CLAIMANT, RIVAL_LAST):
            raise ValueError(f"exclusive.rival {self.exclusive.rival!r}")
        named = {C_ADDRESS, C_NEAR, C_KIND, C_KIND_OR_HOLDER, C_IN_FORCE, C_NAME,
                 C_BRAND, C_ARCHIVED, C_WRONG_KIND}
        for r in self.explain:
            if r.status not in STATUSES:
                raise ValueError(f"rung status {r.status!r}")
            if set(r.require) - named or set(r.forbid) - named:
                raise ValueError(f"rung {r.status}: unknown conditions")
        if set(self.prefer) - named:
            raise ValueError(f"prefer: unknown conditions {self.prefer}")

    def conditions(self) -> tuple[str, ...]:
        """The admission conditions this policy imposes, in the order they are read."""
        out = [C_ADDRESS, C_KIND_OR_HOLDER if self.kinds.holder_override is not None else C_KIND]
        if self.require_in_force:
            out.append(C_IN_FORCE)
        if self.name_policy == NAME_REQUIRED:
            out.append(C_NAME)
        if self.brands.conflict:
            out.append(C_BRAND)
        if self.archived is not None:
            out.append(C_ARCHIVED)
        return tuple(out)


# --------------------------------------------------------------------------
# the result
# --------------------------------------------------------------------------
# One row per object, indexed like the objects.  `record` is the cited register
# row's position (-1 for none): the held record when `assigned`, otherwise the
# near-miss the status talks about.  The pair terms describe that record.
RESULT_COLUMNS = ["status", "assigned", "near_miss", "record", "record_id", "failed_rule",
                  "score", "addr", "name", "house", "distance", "method", "name_kind",
                  "name_basis", "name_via", "kind_match", "unit", "claimed_by", "margin",
                  "generic", "entity"]


@dataclass(frozen=True)
class MatchResult:
    table: pd.DataFrame                              # RESULT_COLUMNS + n_<tally>, ids_<tally>
    brands: Mapping[str, BrandResult] = field(default_factory=dict)  # per block ("" unblocked)

    @property
    def brand(self) -> BrandResult | None:
        """The one learned-brand result of an unblocked run."""
        return next(iter(self.brands.values()), None) if len(self.brands) == 1 else None


# --------------------------------------------------------------------------
# stage 1: candidates
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Sides:
    a: list[pd.DataFrame]              # one object side per address variant
    b: dict[str, pd.DataFrame]         # one register side per name field
    a_keys: np.ndarray                 # object name keys (`fascia_name`)
    brand_keys: np.ndarray             # object brand keys (spellings folded)
    b_keys: dict[str, np.ndarray]      # register name keys per field
    holder_id: np.ndarray
    in_force: np.ndarray
    kind: np.ndarray
    record_id: np.ndarray
    units: np.ndarray                  # the outlet key per register row
    lat: np.ndarray
    lon: np.ndarray


def _str(df: pd.DataFrame, col: str, n: int) -> list[str]:
    return df[col].fillna("").astype(str).tolist() if col in df.columns else [""] * n


def _address_key(s: pd.DataFrame) -> np.ndarray:
    """settlement|street|house|suffix from a prepared side; "" when unparsed."""
    key = (s["settlement"].astype(str) + "|" + s["street"].astype(str) + "|"
           + s["house"].astype(str) + "|" + s["suffix"].astype(str))
    return key.where(s["street"].astype(str) != "", "").to_numpy(dtype=object)


def prepare(objects: pd.DataFrame, register: pd.DataFrame, policy: Policy) -> Sides:
    """Both tables as canonical sides -- the only stage that reads their columns.

    Objects: `name`, `address`, optional `address_2` (a second address of the
    same door: a corner building), `lat`, `lon`.  Register (RegisterTable):
    `record_id`, `holder_id`, `holder_name`, `venue_name`, `address_line`, and
    either `street` / `house` (+ `settlement`, `block`) or `address_line` alone,
    `lat`, `lon`, `in_force`, `kind`.
    """
    city = home_city_re(*policy.home_cities)
    n_a, n_b = len(objects), len(register)
    names = _str(objects, "name", n_a)
    a_keys = np.array([fascia_name(v, aliases=policy.aliases) for v in names], dtype=object)
    sp = policy.brands.spellings
    brand_keys = np.array([sp.get(brand_key(k), brand_key(k)) for k in a_keys], dtype=object)
    first = _str(objects, "address", n_a)
    variants = [first]
    second = _str(objects, "address_2", n_a)
    if any(second):
        variants.append([s or f for f, s in zip(first, second, strict=True)])
    a = [side([parse_address_line(v, city) for v in addrs], a_keys,
              objects["lat"], objects["lon"], alias_keys=brand_keys) for addrs in variants]

    if "street" in register.columns:
        top = [parse_address_fields(s, h, city, sub, b) for s, h, sub, b in zip(
            _str(register, "street", n_b), _str(register, "house", n_b),
            _str(register, "settlement", n_b), _str(register, "block", n_b), strict=True)]
    else:
        top = [parse_address_line(v, city) for v in _str(register, "address_line", n_b)]
    holder_id = np.array(_str(register, "holder_id", n_b), dtype=object)
    b_keys = {
        FIELD_HOLDER: np.array([holder_name(v, aliases=policy.aliases)
                                for v in _str(register, "holder_name", n_b)], dtype=object),
        FIELD_VENUE: np.array([fascia_name(v, aliases=policy.aliases)
                               for v in _str(register, "venue_name", n_b)], dtype=object),
    }
    b = {f: side(top, b_keys[f], register["lat"], register["lon"], alias_keys=holder_id)
         for f in policy.name_fields}

    first_b = b[policy.name_fields[0]]
    raw = np.array(_str(register, "address_line", n_b), dtype=object)
    parts = []
    for p in policy.unit:
        if p == UNIT_TOPONYM:
            k = _address_key(first_b)
            parts.append(np.where(k != "", k, raw))
        elif p == "venue":
            parts.append(b_keys[FIELD_VENUE])
        else:
            parts.append(np.array(_str(register, p, n_b), dtype=object))
    units = holder_id + "@" + (parts[0] if len(parts) == 1 else
                               np.array(["|".join(t) for t in zip(*parts, strict=True)], dtype=object))
    return Sides(a=a, b=b, a_keys=a_keys, brand_keys=brand_keys, b_keys=b_keys,
                 holder_id=holder_id,
                 in_force=register["in_force"].fillna(False).to_numpy(dtype=bool),
                 kind=np.array(_str(register, "kind", n_b), dtype=object),
                 record_id=np.array(_str(register, "record_id", n_b), dtype=object),
                 units=np.asarray(units, dtype=object),
                 lat=pd.to_numeric(objects["lat"], errors="coerce").to_numpy(float),
                 lon=pd.to_numeric(objects["lon"], errors="coerce").to_numpy(float))


def site_keys(s: Sides) -> np.ndarray:
    """One label per physical address on the object side.

    Votes for a brand are counted over distinct addresses, so two records of one
    shop (or one shop in two layers) vote once.  The parsed address decides; an
    unparsed one falls back to the coordinate.
    """
    addr = _address_key(s.a[0])
    lat = pd.Series(s.lat).round(5).astype(str).to_numpy(dtype=object)
    lon = pd.Series(s.lon).round(5).astype(str).to_numpy(dtype=object)
    return np.where(addr != "", addr, lat + "," + lon)


def kind_mask(objects: pd.DataFrame, s: Sides, kinds: Kinds) -> np.ndarray:
    """(objects x records): the record's kind permits what the object is (strict)."""
    n_a, n_b = len(s.lat), len(s.kind)
    if kinds.column is None:
        if kinds.allowed is None:
            return np.ones((n_a, n_b), dtype=bool)
        return np.broadcast_to(np.isin(s.kind, list(kinds.allowed))[None, :], (n_a, n_b)).copy()
    val = objects[kinds.column].astype(str).to_numpy()
    out = np.zeros((n_a, n_b), dtype=bool)
    for v, allowed in kinds.by_value.items():
        out[val == v, :] = np.isin(s.kind, list(allowed))[None, :]
    return out


# --------------------------------------------------------------------------
# stage 2: scoring (brands learned here, since they feed the name term)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Scored:
    scores: Scores                 # with learned aliases, where the policy has them
    plain: Scores                  # the same pairs, names compared as strings only
    brands: BrandResult | None
    known: dict[str, set]          # brand key -> holder ids known to trade under it
    franchise: set                 # brand keys run by several operators
    sites: np.ndarray


def _pick(x: Scores, y: Scores, use_y: np.ndarray) -> Scores:
    """`x` with `y`'s values wherever `use_y`."""
    return Scores(**{k: (np.where(use_y, getattr(y, k), getattr(x, k))
                         if getattr(x, k) is not None else None)
                     for k in x.as_dict()})


def _rank(sc: Scores, params: MatchParams) -> np.ndarray:
    """Admitted pairs first, then by score."""
    return accepted(sc, params) * 10.0 + np.nan_to_num(sc.score, nan=-1.0)


REVIEW_ACCEPT, REVIEW_REJECT = "accept", "reject"


def learn(objects: pd.DataFrame, s: Sides, policy: Policy, kind_ok: np.ndarray,
          sites: np.ndarray, holder_names: Sequence[str]) -> BrandResult:
    """(fascia, holder) pairs learned from the objects `learn_column` selects.

    Scored on the address and distance terms only, reweighted to keep their
    ratio -- the name term would bring the answer in with it.  Only pairs whose
    record permits the object's kind take part, so a bar never votes for the
    shop licence in its building.  `brands.learn_brands` does the learning;
    `Brands.review` is then applied, and the table gains:

    * `active_sites` -- at how many of the addresses that voted for the pair the
      holder's record is in force.  A pair resting on archived records alone
      says who traded there once; it may name the pair but must not bar anyone
      from an in-force record today, so `feeds_conflict` is False for it.
    * `review` / `review_reason` / `used`.
    """
    br = policy.brands
    mask = (objects[br.learn_column].to_numpy(dtype=bool) if br.learn_column
            else np.ones(len(objects), dtype=bool))
    rows_sub = np.flatnonzero(mask)
    a = s.a[0].iloc[rows_sub].reset_index(drop=True)
    b = s.b[FIELD_HOLDER] if FIELD_HOLDER in s.b else next(iter(s.b.values()))
    w_addr, _, w_dist = policy.params.weights
    w_sum = w_addr + w_dist
    # Built afresh rather than `replace()`d: the learning run keeps the package
    # defaults for every knob but the weights and the radius, as it always has.
    p = MatchParams(w_addr=w_addr / w_sum, w_name=0.0, w_dist=w_dist / w_sum,
                    max_m=policy.params.max_m)
    sc = score_matrix(a, b, p)
    ok = accepted(sc, p) & kind_ok[rows_sub]
    strength = np.where(ok, np.nan_to_num(sc.score, nan=0.0), 0.0)
    sub_sites = sites[rows_sub]
    res = learn_brands(strength, a["alias_key"], s.holder_id, sites=sub_sites, units=s.units,
                       holder_names=holder_names, params=br.params)

    t = res.table.copy()
    fk = a["alias_key"].to_numpy(dtype=object)
    votes = res.site_votes or {}
    act_sites = []
    for f, h in zip(t["fascia"], t["holder"], strict=True):
        voted = votes.get((f, h), frozenset())
        rows = np.flatnonzero((fk == f) & np.isin(sub_sites, list(voted)))
        cols = (s.holder_id == h) & s.in_force
        hit = ok[np.ix_(rows, np.flatnonzero(cols))].any(axis=1) if len(rows) and cols.any() \
            else np.zeros(len(rows), dtype=bool)
        act_sites.append(len(set(sub_sites[rows[hit]])))
    t["active_sites"] = act_sites
    keys = list(zip(t["fascia"], t["holder"], strict=True))
    t["review"] = [br.review.get(k, ("", ""))[0] for k in keys]
    t["review_reason"] = [br.review.get(k, ("", ""))[1] for k in keys]
    t["used"] = np.where(t["review"] == REVIEW_REJECT, False,
                         np.where(t["review"] == REVIEW_ACCEPT, True, t["accepted"]))
    t["feeds_conflict"] = t["used"] & (t["active_sites"] > 0)
    pairs = frozenset((f, h) for f, h, u in zip(t["fascia"], t["holder"], t["used"], strict=True) if u)
    return BrandResult(table=t, pairs=pairs, iterations=res.iterations,
                       converged=res.converged, assignment=res.assignment,
                       cycle=res.cycle, oscillating=res.oscillating,
                       site_votes=res.site_votes)


def self_vote_mask(s: Sides, sites: np.ndarray, brands: BrandResult) -> np.ndarray:
    """(objects x records): where a learned pair may not score its own name term.

    A pair that passes the thresholds only with every one of its voting
    addresses (`loo_accepted` False) would confirm itself at those addresses;
    there the name term falls back to the plain string comparison.  A pair a
    person accepted in the review is exempt.
    """
    t = brands.table
    shaky = t[t["used"] & ~t["loo_accepted"] & (t["review"] != REVIEW_ACCEPT)]
    out = np.zeros((len(s.lat), len(s.holder_id)), dtype=bool)
    votes = brands.site_votes or {}
    for f, h in zip(shaky["fascia"], shaky["holder"], strict=True):
        rows = (s.brand_keys == f) & np.isin(sites, list(votes.get((f, h), ())))
        out[np.ix_(rows, s.holder_id == h)] = True
    return out


def known_holders(s: Sides, raw_holders: Sequence[str], policy: Policy,
                  brands: BrandResult | None) -> tuple[dict[str, set], set]:
    """(brand key -> holder ids known to trade under it, franchise brand keys).

    The hand-kept chain table (`Policy.aliases`, matched through the holder's
    own name) plus the learned pairs whose evidence includes an in-force record
    (`feeds_conflict`).
    """
    out: dict[str, set] = {}
    if policy.brands.mode == BRANDS_OFF:
        return out, set()
    sp = policy.brands.spellings
    for key, inn in zip(raw_holders, s.holder_id, strict=True):
        fascia = policy.aliases.get(key)
        if fascia and inn:
            out.setdefault(sp.get(brand_key(fascia), brand_key(fascia)), set()).add(inn)
    franchise: set = set()
    if brands is not None:
        t = brands.table
        for f, h, fr in t.loc[t["feeds_conflict"], ["fascia", "holder", "franchise"]].itertuples(index=False):
            out.setdefault(f, set()).add(h)
            if fr:
                franchise.add(f)
    return out, franchise


def score(objects: pd.DataFrame, s: Sides, policy: Policy, kind_ok: np.ndarray,
          holders: Sequence[str]) -> Scored:
    """Every pair scored; per pair the better name field, then the better address.

    With learned brands the aliased score replaces the plain one except where a
    pair would vote for itself (`self_vote_mask`).  `plain` keeps the string
    comparison of the names, which the brand and kind rules read.
    """
    p = policy.params
    sites = site_keys(s)
    brands = None
    if policy.brands.mode == BRANDS_LEARNED:
        brands = learn(objects, s, policy, kind_ok, sites, holder_names=holders)
    shaky = self_vote_mask(s, sites, brands) if brands is not None and brands.pairs else None

    def for_side(a: pd.DataFrame) -> tuple[Scores, Scores]:
        best = plain_best = None
        for f in policy.name_fields:
            plain = score_matrix(a, s.b[f], p)
            sc = plain
            if shaky is not None:
                sc = _pick(score_matrix(a, s.b[f], p, aliases=brands.pairs), plain, shaky)
            if best is None:
                best, plain_best = sc, plain
            else:
                # Scored once per name field; the address and distance terms
                # are the same, the pair keeps the better name.
                use = np.nan_to_num(sc.name) > np.nan_to_num(best.name)
                best, plain_best = _pick(best, sc, use), _pick(plain_best, plain, use)
        return best, plain_best

    sc, plain = for_side(s.a[0])
    for a in s.a[1:]:
        # A corner building is written as two addresses; each pair keeps the better.
        sc2, plain2 = for_side(a)
        use = _rank(sc2, p) > _rank(sc, p)
        sc, plain = _pick(sc, sc2, use), _pick(plain, plain2, use)
    known, franchise = known_holders(s, _hyphenless(holders), policy, brands)
    return Scored(scores=sc, plain=plain, brands=brands, known=known, franchise=franchise,
                  sites=sites)


def _hyphenless(holders: Sequence[str]) -> list[str]:
    """Holder keys as the alias table is keyed: "альбион-2002" -> "альбион 2002"."""
    return [h.replace("-", " ") for h in holders]


# --------------------------------------------------------------------------
# stage 3: admission
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Admission:
    conditions: dict[str, np.ndarray]   # name -> (objects x records) mask
    admitted: np.ndarray                # AND of the policy's conditions
    name_by: dict[str, np.ndarray]      # name field -> agreement mask (name required)


def brand_conflict(s: Sides, known: dict[str, set], franchise: set, name: np.ndarray,
                   conflict_name: float) -> np.ndarray:
    """(objects x records) mask of pairs the known brands rule out, both ways.

    * **The object is a known brand**: it holds only its own holders' records.
      A Пятёрочка is never licensed by ООО «Южанка».  A franchise (several
      operators, not all of them necessarily learned) is exempt here.
    * **The record is a known brand's**: it goes only to an object of one of
      that holder's fascias, or to one whose name matches the holder's outright
      (`conflict_name`).  A «Пивная лавка» next door does not hold Агроторг's
      licence.

    An object whose fascia is unknown meeting a record whose holder is unknown
    is never in conflict -- the rule applies only what is already known.
    """
    inn, fk = s.holder_id, s.brand_keys
    fascias_of: dict[str, set] = {}
    for f, hs in known.items():
        for h in hs:
            fascias_of.setdefault(h, set()).add(f)
    out = np.zeros((len(fk), len(inn)), dtype=bool)
    # forward: a known, non-franchise fascia takes only its own holders
    for f in pd.unique(fk):
        own = known.get(f)
        if own and f not in franchise:
            out[fk == f, :] |= ~np.isin(inn, list(own))[None, :]
    # reverse: a known holder's record goes only to its own fascias
    for h, fs in fascias_of.items():
        cols = np.flatnonzero(inn == h)
        if not len(cols):
            continue
        foreign = ~np.isin(fk, list(fs))
        out[np.ix_(foreign, cols)] |= name[np.ix_(foreign, cols)] < conflict_name
    return out


def admit(s: Sides, sc: Scored, policy: Policy, kind_ok: np.ndarray) -> Admission:
    """Every named condition as a mask, and the pairs passing the policy's list."""
    p = policy.params
    ok = accepted(sc.scores, p)
    n_a, n_b = ok.shape
    alias = sc.scores.name_kind == NAME_ALIAS
    cond: dict[str, np.ndarray] = {
        C_ADDRESS: ok,
        C_NEAR: sc.scores.method == ROUTE_GEO,
        C_KIND: kind_ok,
        C_IN_FORCE: np.broadcast_to(s.in_force[None, :], (n_a, n_b)),
        C_WRONG_KIND: np.broadcast_to(np.isin(s.kind, list(policy.kinds.wrong))[None, :], (n_a, n_b)),
    }
    if policy.kinds.holder_override is not None:
        cond[C_KIND_OR_HOLDER] = kind_ok | alias | (sc.plain.name >= policy.kinds.holder_override)
    name_by: dict[str, np.ndarray] = {}
    if policy.name_policy == NAME_REQUIRED:
        for f in policy.name_fields:
            name_by[f] = agree_matrix(s.a_keys, s.b_keys[f], policy.place_words)
        cond[C_NAME] = np.logical_or.reduce(list(name_by.values()))
    if policy.brands.conflict:
        cond[C_BRAND] = ~brand_conflict(s, sc.known, sc.franchise, sc.plain.name,
                                        policy.brands.conflict_name)
    if policy.archived is not None:
        own_holder = np.zeros((n_a, n_b), dtype=bool)
        for f, hs in sc.known.items():
            own_holder[np.ix_(s.brand_keys == f, np.isin(s.holder_id, list(hs)))] = True
        live_here = (ok & s.in_force[None, :] & kind_ok).any(axis=1)
        agrees = alias | (sc.plain.name >= policy.archived.name_min) | own_holder
        cond[C_ARCHIVED] = s.in_force[None, :] | agrees | ~live_here[:, None]
    admitted = np.logical_and.reduce([cond[c] for c in policy.conditions()])
    return Admission(conditions=cond, admitted=admitted, name_by=name_by)


# --------------------------------------------------------------------------
# stage 4: assignment
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Assignment:
    ranked: np.ndarray       # (objects x records) the value the assignment ranks by
    best: np.ndarray         # (objects,) the top admitted record, ignoring exclusivity
    pick: np.ndarray         # (objects,) the held record where hit, else best
    hit: np.ndarray
    taken: np.ndarray
    rival: np.ndarray        # (objects,) who holds the unit of its best record, -1
    entity: np.ndarray       # (objects,) entity label


def entities(s: Sides, names: Sequence[str], rule: Entities) -> np.ndarray:
    """One label per shop, however many records the export gives it.

    Two layers of one export can carry one store twice, and a layer often twice
    over ("Пятёрочка" and "Пятёрочка, супермаркет" at one coordinate).  Without
    this the one-unit rule makes a shop compete with itself.
    """
    n = len(s.lat)
    d = haversine_m(s.lat[:, None], s.lon[:, None], s.lat[None, :], s.lon[None, :])
    key = s.brand_keys
    raw = np.array([fold(v) for v in names], dtype=object)
    gen = np.array([is_generic(k) or len(k) < 3 for k in key], dtype=bool)
    uk, inv = np.unique(key, return_inverse=True)
    sim = np.asarray(process.cdist(list(uk), list(uk), scorer=fuzz.token_set_ratio,
                                   workers=-1))[inv][:, inv]
    same_brand = ((key[:, None] == key[None, :]) | (sim >= rule.name_min)) \
        & ~gen[:, None] & ~gen[None, :]
    link = ((d <= rule.max_m) & same_brand) | ((d <= rule.same_name_m)
                                                & (raw[:, None] == raw[None, :]))
    parent = np.arange(n)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in zip(*np.nonzero(np.triu(link, 1)), strict=True):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)
    return np.array([find(i) for i in range(n)])


def _codes(labels, n: int) -> np.ndarray:
    if labels is None:
        return np.zeros(n, dtype=int)
    return pd.factorize(pd.Series(list(labels), dtype=object))[0]


def greedy(value: np.ndarray, allowed: np.ndarray, units: np.ndarray, partition: np.ndarray,
           same: Callable[[int, int], bool]) -> tuple[np.ndarray, dict]:
    """One unit per object, strongest pair first; `same(i, claimant)` may share.

    `select.greedy_assign` with the sharing test as a function: the first
    object to claim a unit holds it, and a later one may hold it too only if it
    is the same shop as that claimant.  Ties go to the lower object index, then
    the lower column.  Returns (pick per object, (partition, unit) -> claimant).
    """
    n_a = value.shape[0]
    unit = _codes(units, len(units))
    ii, jj = np.nonzero(allowed)
    order = np.lexsort((jj, ii, -value[ii, jj]))
    pick = np.full(n_a, -1, dtype=int)
    claimed: dict = {}
    for k in order:
        i, j = ii[k], jj[k]
        if pick[i] >= 0:
            continue
        key = (partition[i], unit[j])
        if key in claimed and not same(i, claimed[key]):
            continue
        claimed.setdefault(key, i)
        pick[i] = j
    return pick, claimed


def assign(objects: pd.DataFrame, s: Sides, adm: Admission, sc: Scored, policy: Policy) -> Assignment:
    """Rank the admitted pairs, then give out the units.

    A preferred pair (`Policy.prefer`, highest first) beats every non-preferred
    one whatever their scores; with several levels the k-th from the bottom is
    lifted by PREFER_LIFT * 2**k, so each level outranks every combination of
    the ones below it.  Greedy rather than an optimal assignment on purpose --
    "the strongest claim wins" is a rule a reader can check by eye.
    """
    ok = adm.conditions[C_ADDRESS]
    n_a, n_b = ok.shape
    ranked = np.where(ok, np.nan_to_num(sc.scores.score, nan=0.0), -1.0)
    for k, c in enumerate(policy.prefer):
        lift = PREFER_LIFT * 2.0 ** (len(policy.prefer) - 1 - k)
        ranked = np.where(ok & adm.conditions[c], ranked + lift, ranked)
    choosable = adm.admitted
    has_choice = choosable.any(axis=1) if n_b else np.zeros(n_a, dtype=bool)
    best = (np.argmax(np.where(choosable, ranked, -np.inf), axis=1) if n_b
            else np.zeros(n_a, dtype=int))

    ent = np.arange(n_a)
    if policy.entities is not None:
        ent = entities(s, _str(objects, "name", n_a), policy.entities)
    ex = policy.exclusive
    rival = np.full(n_a, -1, dtype=int)
    if ex is None or not n_b:
        pick, hit = best, has_choice
    else:
        part = _codes(objects[ex.scope] if ex.scope else None, n_a)
        if ex.share == SHARE_ENTITY:
            def same(i, w):
                return ent[i] == ent[w]
        else:
            def same(i, w):
                return (s.a_keys[i] == s.a_keys[w]
                        or names_agree(s.a_keys[i], s.a_keys[w], policy.place_words))
        allowed = choosable
        if not ex.fallback:
            # A loser does not move on: each object competes for its best unit only.
            allowed = np.zeros_like(choosable)
            allowed[np.flatnonzero(has_choice), best[has_choice]] = True
        chosen, claimed = greedy(ranked, allowed, s.units, part, same)
        hit = chosen >= 0
        pick = np.where(hit, chosen, best)
        unit = _codes(s.units, n_b)
        if ex.rival == RIVAL_LAST:
            claimed = {(part[i], unit[pick[i]]): i for i in np.flatnonzero(hit)}
        for i in np.flatnonzero(has_choice & ~hit):
            rival[i] = claimed.get((part[i], unit[best[i]]), -1)
    return Assignment(ranked=ranked, best=best, pick=pick, hit=hit,
                      taken=has_choice & ~hit, rival=rival, entity=ent)


# --------------------------------------------------------------------------
# stage 5: explanation
# --------------------------------------------------------------------------
def explain(s: Sides, sc: Scored, adm: Admission, asg: Assignment, policy: Policy,
            holders: Sequence[str]) -> pd.DataFrame:
    """Status, cited record and its terms per object (positions, not labels)."""
    n_a, n_b = adm.admitted.shape
    raw = np.nan_to_num(sc.scores.score, nan=-1.0)
    status = np.full(n_a, NONE, dtype=object)
    record = np.full(n_a, -1, dtype=int)
    failed = np.full(n_a, "", dtype=object)
    conds = policy.conditions()

    def best_in(mask_row: np.ndarray, i: int) -> int:
        row = np.where(mask_row, raw[i], -np.inf)
        return int(np.argmax(row)) if np.isfinite(row).any() else -1

    rungs = [(r, np.logical_and.reduce([adm.conditions[c] for c in r.require])
              & ~np.logical_or.reduce([adm.conditions[c] for c in r.forbid] or
                                      [np.zeros((n_a, n_b), dtype=bool)]))
             for r in policy.explain]
    for i in range(n_a):
        if asg.hit[i]:
            j = asg.pick[i]
            status[i], record[i] = (OWN if s.in_force[j] else INACTIVE), j
        elif asg.taken[i]:
            status[i], record[i], failed[i] = TAKEN, asg.best[i], C_EXCLUSIVE
        else:
            for r, m in rungs:
                j = best_in(m[i], i) if n_b else -1
                if j >= 0:
                    status[i], record[i] = r.status, j
                    failed[i] = "+".join(c for c in conds if not adm.conditions[c][i, j])
                    break

    got = record >= 0
    rows = np.arange(n_a)
    j = np.where(got, record, 0)

    def at(m: np.ndarray, empty=np.nan) -> np.ndarray:
        if not n_b:
            return np.full(n_a, empty, dtype=object if isinstance(empty, str) else float)
        return np.where(got, m[rows, j], empty)

    kinds = np.array([NAME_KINDS[k] for k in sorted(NAME_KINDS)], dtype=object)
    name_kind = (np.where(got, kinds[sc.scores.name_kind[rows, j]], "") if n_b
                 and sc.scores.name_kind is not None else np.full(n_a, "", dtype=object))
    methods = np.array(["", ROUTE_NAMES[0], ROUTE_NAMES[1]], dtype=object)
    method = np.where(got, methods[sc.scores.method[rows, j] + 1], "") if n_b else np.full(n_a, "")

    # Where the cited pair's name term came from: the hand-kept table when the
    # holder's own name maps onto the object's fascia key, else the score's kind.
    manual_of = np.array([policy.aliases.get(h) for h in _hyphenless(holders)], dtype=object)
    is_manual = np.array([got[i] and manual_of[j[i]] is not None and manual_of[j[i]] == s.a_keys[i]
                          for i in range(n_a)], dtype=bool)
    basis = np.where(~got, "", np.where(is_manual, BASIS_MANUAL,
                                        np.where(name_kind == "alias", BASIS_LEARNED,
                                                 np.where(name_kind == "generic", BASIS_GENERIC,
                                                          BASIS_FUZZY))))
    via = np.full(n_a, "", dtype=object)
    for f in reversed(policy.name_fields):
        if f in adm.name_by:
            via = np.where(got & at(adm.name_by[f], False).astype(bool), f, via)

    # How narrowly the unit was lost: the holder's score on it minus this
    # object's best admitted score.
    margin = np.full(n_a, np.nan)
    tk = np.flatnonzero(asg.taken & (asg.rival >= 0))
    if len(tk):
        best_raw = np.argmax(np.where(adm.admitted, raw, -np.inf), axis=1)
        margin[tk] = raw[asg.rival[tk], asg.pick[asg.rival[tk]]] - raw[tk, best_raw[tk]]

    generic = np.array([(is_generic_name(k) if policy.name_policy == NAME_REQUIRED
                         else is_generic(k)) for k in s.a_keys], dtype=bool)
    return pd.DataFrame({
        "status": status, "assigned": asg.hit.astype(bool), "near_miss": got & ~asg.hit,
        "record": record,
        "record_id": np.where(got, s.record_id[j] if n_b else "", ""),
        "failed_rule": failed,
        "score": at(sc.scores.score), "addr": at(sc.scores.addr), "name": at(sc.scores.name),
        "house": at(sc.scores.house), "distance": at(sc.scores.distance),
        "method": method, "name_kind": name_kind, "name_basis": basis, "name_via": via,
        "kind_match": at(adm.conditions[C_KIND], False).astype(bool) & got,
        "unit": np.where(got, s.units[j] if n_b else "", ""),
        "claimed_by": asg.rival, "margin": margin, "generic": generic, "entity": asg.entity,
    })


def tallies(s: Sides, adm: Admission, policy: Policy) -> dict[str, list]:
    """Distinct record ids per object among the admitted-by-address records.

    Over distinct ids, not rows: a register that files one licence once per
    status change would otherwise count it several times.
    """
    ok = adm.conditions[C_ADDRESS]
    out: dict[str, list] = {}
    for name, t in policy.tallies.items():
        m = ok.copy()
        if t.in_force is not None:
            m &= (s.in_force == t.in_force)[None, :]
        if t.kinds is not None:
            m &= np.isin(s.kind, list(t.kinds))[None, :]
        sets = [set(s.record_id[np.flatnonzero(r)]) for r in m]
        out[f"n_{name}"] = [len(x) for x in sets]
        out[f"ids_{name}"] = ["; ".join(sorted(x)) for x in sets]
    return out


# --------------------------------------------------------------------------
# the function
# --------------------------------------------------------------------------
def _empty(n: int, policy: Policy) -> pd.DataFrame:
    out = pd.DataFrame({
        "status": NONE, "assigned": False, "near_miss": False, "record": -1, "record_id": "",
        "failed_rule": "", "score": np.nan, "addr": np.nan, "name": np.nan, "house": np.nan,
        "distance": np.nan, "method": "", "name_kind": "", "name_basis": "", "name_via": "",
        "kind_match": False, "unit": "", "claimed_by": -1, "margin": np.nan,
        "generic": False, "entity": -1}, index=range(n))
    for name in policy.tallies:
        out[f"n_{name}"] = 0
        out[f"ids_{name}"] = ""
    return out


def _run(objects: pd.DataFrame, register: pd.DataFrame, policy: Policy
         ) -> tuple[pd.DataFrame, BrandResult | None]:
    """The five stages over one block; positions are local to the block."""
    if not len(objects) or not len(register):
        return _empty(len(objects), policy), None
    holders = [holder_name(v, aliases={}) for v in _str(register, "holder_name", len(register))]
    s = prepare(objects, register, policy)
    kind_ok = kind_mask(objects, s, policy.kinds)
    sc = score(objects, s, policy, kind_ok, holders)
    adm = admit(s, sc, policy, kind_ok)
    asg = assign(objects, s, adm, sc, policy)
    out = explain(s, sc, adm, asg, policy, holders)
    for k, v in tallies(s, adm, policy).items():
        out[k] = v
    return out, sc.brands


def match(objects: pd.DataFrame, register: pd.DataFrame, policy: Policy | None = None) -> MatchResult:
    """Match every object against the register under one policy.

    Pure: no files, no network, no module state; the same inputs give the same
    table.  Both tables are read by position, and the result is indexed like
    `objects`.  `record` is a position into `register` (-1 for none);
    `claimed_by` an index label of `objects` (None where nothing was claimed);
    `entity` a label shared by the records of one shop.
    """
    policy = policy or Policy()
    labels = objects.index
    objects = objects.reset_index(drop=True)
    register = register.reset_index(drop=True)
    if policy.block is None:
        blocks = [("", np.arange(len(objects)), np.arange(len(register)))]
    else:
        ob = objects[policy.block].fillna("").astype(str).to_numpy()
        rb = register[policy.block].fillna("").astype(str).to_numpy()
        # An empty block value meets nothing.
        blocks = [(v, np.flatnonzero(ob == v), np.flatnonzero(rb == v) if v else np.array([], int))
                  for v in pd.unique(ob)]
    parts, brands = [], {}
    for v, oi, ri in blocks:
        out, br = _run(objects.iloc[oi].reset_index(drop=True),
                       register.iloc[ri].reset_index(drop=True), policy)
        # local positions -> register rows and object labels
        rec = out["record"].to_numpy(int)
        out["record"] = np.where(rec >= 0, ri[np.maximum(rec, 0)] if len(ri) else -1, -1)
        out["claimed_by"] = [labels[oi[c]] if c >= 0 else None for c in out["claimed_by"]]
        out["entity"] = [f"{v}:{e}" if e >= 0 else "" for e in out["entity"]]
        out.index = oi
        parts.append(out)
        if br is not None:
            brands[v] = br
    table = pd.concat(parts).sort_index() if parts else _empty(0, policy)
    table.index = labels
    return MatchResult(table=table, brands=brands)


__all__ = [
    "match", "MatchResult", "Policy", "Kinds", "Brands", "Archived", "Entities",
    "Exclusive", "Rung", "Tally", "STATUSES", "RESULT_COLUMNS",
    "OWN", "INACTIVE", "TAKEN", "OTHER_HOLDER", "OTHER_CHAIN", "WRONG_KIND", "ELSEWHERE", "NONE",
    "C_ADDRESS", "C_NEAR", "C_KIND", "C_KIND_OR_HOLDER", "C_IN_FORCE", "C_NAME", "C_BRAND",
    "C_ARCHIVED", "C_WRONG_KIND", "C_EXCLUSIVE",
    "prepare", "score", "admit", "assign", "explain", "tallies", "entities", "site_keys",
]
