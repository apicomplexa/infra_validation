"""Which company trades under which fascia, learned from where they stand.

A register names the licence *holder*, a directory the *shop*, and for anything
bigger than a single shop the two never resemble each other.  `names` carries a
hand-kept list for the national chains; this module finds the rest in the data.

The idea is a vote across addresses.  Score every object against every register
row on the address and distance terms only -- the name term would bring the
answer in with it.  A fascia's real holder sits at nearly every one of its
addresses; the neighbours at each address differ from one address to the next.
Summed over a fascia's addresses, the real holder accumulates and the neighbours
do not.

Two corrections make the vote honest:

* **Lift over the base rate.**  A national chain stands next to a large share of
  every city's shops, so it collects votes from any fascia at all.  A holder is
  credited to a fascia only when it appears at the fascia's addresses several
  times more often than at addresses in general.
* **Competition for the outlet.**  A tobacco kiosk that always stands inside a
  Пятёрочка would out-vote nothing and still collect Агроторг at every address.
  So the votes are not presence counts but *assignments*: the register's
  outlets are handed out one object each (`select.greedy_assign`), a vote is an
  outlet an object actually received, and the fascias' current votes are fed
  back as a prior on the next round.  The supermarket, whose vote for Агроторг
  is backed by its other addresses, keeps the outlet; the kiosk loses it.  The
  prior an object receives never counts its own address (leave-one-site-out),
  so no pair can vote for itself.

The rounds stop when the assignment no longer changes.  What comes out is a
table, one row per (fascia, holder) with its evidence, and a set of accepted
pairs to hand to `scoring.score_matrix(aliases=...)`.  The table is meant to be
read: a learned pair is a hypothesis with a count behind it, not a fact.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from .names import GENERIC_FASCIAS, brand_key
from .select import greedy_assign


@dataclass(frozen=True)
class BrandParams:
    """The thresholds a learned pair must clear, and the prior's two knobs."""
    # Distinct addresses of the fascia at which the holder's outlet was
    # *assigned* to it.  Three is the fewest at which "every time" means more
    # than coincidence; a fascia with fewer addresses is never learned.
    min_sites: int = 3
    # Share of the fascia's addresses (with any candidate) that the holder took.
    # 0.3 rather than a majority, so that a franchise run by two operators can
    # yield both; the lift and the site count carry the burden of proof.
    min_share: float = 0.3
    # How much more often the holder stands at this fascia's addresses than at
    # addresses in general.
    min_lift: float = 3.0
    # A fascia no single holder dominates -- its best holder took fewer than
    # `franchise_top_max` of its addresses -- is a franchise run by several
    # operators ("Петровский" in Arkhangelsk: 25 addresses, five operators or
    # more, the largest with 28 %).  Each of its operators needs only
    # `franchise_min_share`; the site count and the lift still apply.  A fascia
    # one holder dominates keeps `min_share` for any second holder, which is
    # what keeps a neighbour out of a national chain's column.
    franchise_top_max: float = 0.5
    franchise_min_share: float = 0.1
    # ...and must pass a test from the holder's side: at least this share of
    # the holder's outlets (those any object reached) stand at the fascia's
    # addresses.  A franchise operator is mostly that franchise; a company whose
    # twenty outlets merely include a few of the fascia's buildings is a
    # neighbour.  The franchise rule was introduced for Петровский, and this is
    # the test that keeps it from admitting co-location elsewhere.
    franchise_holder_cover: float = 0.5
    # Weight of the fascia prior against the pair's own score (0..1 scale).
    prior_weight: float = 0.15
    # The prior is log((c + s*base) / (n + s)) - log(base): zero for a fascia
    # with no other addresses, positive for a holder its other addresses chose,
    # negative for one they never did.
    base: float = 0.05
    smoothing: float = 1.0
    max_iter: int = 20


BRAND_DEFAULTS = BrandParams()


@dataclass(frozen=True)
class BrandResult:
    table: pd.DataFrame          # one row per (fascia, holder) with evidence
    pairs: frozenset             # accepted (fascia key, holder) pairs
    iterations: int
    converged: bool
    assignment: np.ndarray       # final chosen column per object, -1 for none
    # 0 converged; 2 stopped on a two-round cycle (the objects that alternate
    # vote for nothing); -1 stopped at max_iter.
    cycle: int = 0
    oscillating: int = 0         # objects whose pick alternated in the cycle
    # (fascia, holder) -> the sites whose assignment voted for the pair.  An
    # adapter needs it to keep a pair from confirming itself at the very
    # addresses that produced it (see the `loo_accepted` column).
    site_votes: dict | None = None


def learn_brands(strength: np.ndarray, fascia: Sequence[str], holders: Sequence,
                 *, sites: Sequence | None = None, units: Sequence | None = None,
                 holder_names: Sequence[str] | None = None,
                 generic=GENERIC_FASCIAS,
                 params: BrandParams = BRAND_DEFAULTS) -> BrandResult:
    """Learn (fascia, holder) pairs from an (objects x register rows) strength matrix.

    ``strength``     the score of each pair, NaN or <= 0 where the pair is not
                     admitted.  Build it without the name term.
    ``fascia``       the objects' name keys (`names.fascia_name`).
    ``holders``      the register rows' holder identity -- a tax number is best.
    ``sites``        the objects' address identity; votes are counted over
                     distinct sites, so two records of one shop vote once.
                     Defaults to one site per object.
    ``units``        the register rows' outlet identity (see `select`);
                     defaults to one unit per row.
    ``holder_names`` a readable name per register row, carried into the table.
    """
    strength = np.nan_to_num(np.asarray(strength, dtype=float), nan=0.0)
    n_a, n_b = strength.shape
    allowed = strength > 0
    fk = np.array([brand_key(str(f)) for f in fascia], dtype=object)
    hold = np.array([str(h) for h in holders], dtype=object)
    site = (np.arange(n_a).astype(str) if sites is None
            else np.array([str(s) for s in sites], dtype=object))
    unit = np.arange(n_b) if units is None else list(units)
    if len(fk) != n_a or len(site) != n_a or len(hold) != n_b:
        raise ValueError("fascia/sites must match rows, holders must match columns")
    is_gen = np.array([f in generic for f in fk], dtype=bool)
    has = allowed.any(axis=1)

    # Distinct candidate sites per fascia, per holder, and overall.
    f_sites: dict[str, set] = defaultdict(set)
    fh_present: dict[tuple, set] = defaultdict(set)
    h_present: dict[str, set] = defaultdict(set)
    all_sites: set = set()
    for i in np.flatnonzero(has):
        all_sites.add(site[i])
        hs = set(hold[np.flatnonzero(allowed[i])])
        for h in hs:
            h_present[h].add(site[i])
        if not is_gen[i]:
            f_sites[fk[i]].add(site[i])
            for h in hs:
                fh_present[(fk[i], h)].add(site[i])

    # votes[(f, h)] -> {site: n objects of f at that site assigned to h}
    # The first round is seeded with *presence* (every admitted holder at every
    # site), not with no prior at all.  Unseeded, round one is decided by raw
    # score and object order alone: a kiosk listed before the supermarket it
    # stands in takes the shared outlet on a tie, collects a full vote from
    # it, and the next round confirms the kiosk -- a stable wrong fixed point.
    # Seeded, the supermarket's other addresses weigh in from the start.
    votes: dict[tuple, Counter] = defaultdict(Counter)
    for key, present in fh_present.items():
        votes[key] = Counter({s: 1 for s in present})
    def tally(p: np.ndarray, voters: np.ndarray) -> dict[tuple, Counter]:
        out: dict[tuple, Counter] = defaultdict(Counter)
        for i in np.flatnonzero((p >= 0) & voters & ~is_gen):
            out[(fk[i], hold[p[i]])][site[i]] += 1
        return out

    pick = np.full(n_a, -1, dtype=int)
    before = None                 # the assignment two rounds back
    converged = False
    cycle, oscillating = -1, 0
    it = 0
    ii, jj = np.nonzero(allowed)
    for it in range(1, params.max_iter + 1):
        value = strength.copy()
        if votes:
            for i, j in zip(ii, jj):
                if is_gen[i]:
                    continue
                f, h, s = fk[i], hold[j], site[i]
                own = votes.get((f, h))
                c = 0 if own is None else sum(1 for k in own if k != s and own[k] > 0)
                n = len(f_sites[f] - {s})
                prior = (np.log((c + params.smoothing * params.base) / (n + params.smoothing))
                         - np.log(params.base))
                value[i, j] += params.prior_weight * prior
        new_pick = greedy_assign(value, allowed, unit)
        if np.array_equal(new_pick, pick):
            converged, cycle = True, 0
            break
        if before is not None and np.array_equal(new_pick, before):
            # Two states alternate: a handful of objects trade one outlet back
            # and forth, each round's winner handing the next round's prior to
            # the loser.  Neither state is the answer, so the objects that
            # alternate vote for nothing and the rest keep their stable votes.
            stable = new_pick == pick
            cycle, oscillating = 2, int((~stable).sum())
            votes = tally(pick, stable)
            break
        before, pick = pick, new_pick
        votes = tally(pick, np.ones(n_a, dtype=bool))

    names_of: dict[str, str] = {}
    if holder_names is not None:
        for h, nm in zip(hold, holder_names):
            names_of.setdefault(h, str(nm))

    # The holder's side: which fascias reach each of its outlets.
    unit_arr = np.asarray(unit, dtype=object)
    h_units: dict[str, set] = defaultdict(set)
    fh_units: dict[tuple, set] = defaultdict(set)
    for i, j in zip(ii, jj):
        h_units[hold[j]].add(unit_arr[j])
        if not is_gen[i]:
            fh_units[(fk[i], hold[j])].add(unit_arr[j])

    n_all = max(len(all_sites), 1)
    rows = []
    for (f, h), present in fh_present.items():
        n_f = len(f_sites[f])
        assigned = len(votes.get((f, h), {}))
        base_rate = len(h_present[h]) / n_all
        rows.append({
            "fascia": f, "holder": h, "holder_name": names_of.get(h, ""),
            "fascia_sites": n_f, "sites_present": len(present),
            "sites_assigned": assigned, "share": assigned / n_f,
            "cover": len(present) / n_f, "base_rate": base_rate,
            "lift": (len(present) / n_f) / base_rate if base_rate else float("inf"),
            "holder_cover": len(fh_units[(f, h)]) / max(len(h_units[h]), 1),
        })
    cols = ["fascia", "holder", "holder_name", "fascia_sites", "sites_present",
            "sites_assigned", "share", "cover", "base_rate", "lift", "holder_cover",
            "franchise", "accepted", "loo_accepted", "holders_for_fascia"]
    table = pd.DataFrame(rows, columns=cols[:11])
    table = table[table["sites_assigned"] > 0].reset_index(drop=True)
    top = table.groupby("fascia")["share"].transform("max")
    table["franchise"] = top < params.franchise_top_max

    def passes(n, n_f, present, base, franchise, hcover):
        n_f = np.maximum(n_f, 1)
        share = n / n_f
        lift = np.where(base > 0, (present / n_f) / np.where(base > 0, base, 1), np.inf)
        share_ok = (share >= params.min_share) | (
            franchise & (share >= params.franchise_min_share)
            & (hcover >= params.franchise_holder_cover))
        return (n >= params.min_sites) & share_ok & (lift >= params.min_lift)

    args = (table["franchise"].to_numpy(bool), table["holder_cover"].to_numpy(float))
    base = table["base_rate"].to_numpy(float)
    table["accepted"] = passes(table["sites_assigned"].to_numpy(), table["fascia_sites"].to_numpy(),
                               table["sites_present"].to_numpy(), base, *args)
    # Leave one site out: would the pair still pass with any single one of the
    # addresses that voted for it removed?  A pair that passes only with all of
    # them must not score the name term at those addresses -- it would be
    # confirming itself.
    table["loo_accepted"] = passes(table["sites_assigned"].to_numpy() - 1,
                                   table["fascia_sites"].to_numpy() - 1,
                                   table["sites_present"].to_numpy() - 1, base, *args)
    table["holders_for_fascia"] = (table.groupby("fascia")["accepted"]
                                   .transform("sum").astype(int))
    table = (table.sort_values(["accepted", "sites_assigned", "fascia"],
                               ascending=[False, False, True])
             .reset_index(drop=True)[cols])
    for c, nd in (("share", 3), ("cover", 3), ("base_rate", 4), ("lift", 2),
                  ("holder_cover", 3)):
        table[c] = table[c].round(nd)
    pairs = frozenset(zip(table.loc[table["accepted"], "fascia"],
                          table.loc[table["accepted"], "holder"]))
    site_votes = {k: frozenset(v) for k, v in votes.items() if v}
    return BrandResult(table=table, pairs=pairs, iterations=it,
                       converged=converged, assignment=pick,
                       cycle=cycle, oscillating=oscillating, site_votes=site_votes)


__all__ = ["BrandParams", "BRAND_DEFAULTS", "BrandResult", "learn_brands"]
