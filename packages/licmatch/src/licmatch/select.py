"""From a scored matrix to one chosen counterpart per object, and to counts.

Two decisions live here, and they are separate on purpose:

* **Admission** -- which pairs clear the floors.  Every admitted register row
  counts toward the per-object tallies, because "how many licences sit at this
  address" is a different question from "which one do we quote".
* **Choice** -- which single admitted row is copied out.  A preferred row (in
  practice: a licence in force) beats every non-preferred one whatever their
  scores, so a per-object status can never read "archived" while a live row it
  could have had sits at the same address.

Tallies are over **distinct keys**, not over matrix rows: a register that files
one licence once per status change would otherwise count it several times.

Choice has two optional constraints, both off by default:

* **Eligibility** -- a mask of pairs that may be *chosen*.  An admitted pair
  outside it still counts toward the tallies (it sits at the address), but it
  is never copied out.  An object whose admitted pairs are all ineligible is
  reported as `blocked`, not as unmatched.
* **Exclusivity** -- with `units` given, one register *unit* (an outlet: a
  licence at one address, however many rows the register filed it under) goes
  to at most one object.  Without it every object picks independently, and
  five shops in one building can all quote the one licence filed there.  The
  assignment is greedy on the ranked value: the strongest pair anywhere takes
  its unit, the next strongest takes what is left, and an object whose unit
  went elsewhere falls back to its next free one.  Greedy rather than an
  optimal assignment on purpose -- "the strongest claim wins" is a rule a
  reader can check by eye, and an optimum that swaps two weaker claims to
  raise a total is not.  An object left with nothing is `taken`, and `rival`
  names the object that took its best unit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .scoring import (DEFAULTS, NAME_KINDS, ROUTE_NAMES, MatchParams, Scores,
                      accepted)

# How far a preferred counterpart is lifted above a non-preferred one.  Any
# value above the score range (0..1) has the same effect; 2.0 keeps the lifted
# values readable in a debugger.  With several preference levels the k-th from
# the bottom is lifted by PREFER_LIFT * 2**k, so each level outranks every
# combination of the ones below it.
PREFER_LIFT = 2.0


@dataclass(frozen=True)
class Selection:
    """One row per object: the chosen counterpart, its terms, and the tallies."""
    accepted: np.ndarray                      # (n_a x n_b) bool
    pick: np.ndarray                          # (n_a,) column index into side b
    hit: np.ndarray                           # (n_a,) whether anything was chosen
    score: np.ndarray                         # gathered terms, NaN where no hit
    addr: np.ndarray
    name: np.ndarray
    distance: np.ndarray
    house: np.ndarray
    method: np.ndarray                        # "geo" / "address" / "" as objects
    counts: Mapping[str, np.ndarray] = field(default_factory=dict)
    keys: Mapping[str, list[set]] = field(default_factory=dict)
    name_kind: np.ndarray | None = None       # "fuzzy" / "alias" / "generic" / ""
    taken: np.ndarray | None = None           # (n_a,) eligible candidates, all claimed
    rival: np.ndarray | None = None           # (n_a,) who claimed its best unit, -1
    blocked: np.ndarray | None = None         # (n_a,) admitted, nothing eligible

    def joined(self, group: str, sep: str = "; ") -> list[str]:
        """The distinct keys of one group per object, sorted and joined."""
        return [sep.join(sorted(s)) for s in self.keys[group]]

    def take(self, values: Sequence, empty="") -> np.ndarray:
        """Gather a side-b column onto the objects, `empty` where there is no hit."""
        vals = np.asarray(values, dtype=object)
        return np.where(self.hit, vals[self.pick], empty)


def _gather(matrix: np.ndarray, rows: np.ndarray, pick: np.ndarray,
            hit: np.ndarray) -> np.ndarray:
    return np.where(hit, matrix[rows, pick], np.nan)


def _mask(m, n_a: int, n_b: int, what: str) -> np.ndarray:
    """A side-b (n_b,) or pair (n_a, n_b) boolean mask, as (n_a, n_b)."""
    m = np.asarray(m, dtype=bool)
    if m.shape == (n_b,):
        return np.broadcast_to(m[None, :], (n_a, n_b))
    if m.shape == (n_a, n_b):
        return m
    raise ValueError(f"{what} must have shape ({n_b},) or ({n_a}, {n_b}), got {m.shape}")


def _codes(labels: Sequence | None, n: int, what: str) -> np.ndarray:
    """Integer codes for a label column; all zeros when there are no labels."""
    if labels is None:
        return np.zeros(n, dtype=int)
    codes = pd.factorize(pd.Series(list(labels), dtype=object))[0]
    if len(codes) != n:
        raise ValueError(f"{what} must have length {n}, got {len(codes)}")
    return codes


def greedy_assign(value: np.ndarray, allowed: np.ndarray, units: Sequence,
                  partition: Sequence | None = None,
                  entities: Sequence | None = None) -> np.ndarray:
    """One unit per object and one object per unit, strongest pair first.

    ``value``     (n_a x n_b) ranked values; only ``allowed`` pairs are read.
    ``units``     side-b unit labels: columns sharing a label are one unit.
    ``partition`` side-a group labels: exclusivity holds *within* a group, so
                  two layers of one export can each claim the same unit.
    ``entities``  side-a entity labels: records sharing one are one shop
                  written twice (two files, two spellings), and may all hold
                  the unit any of them holds.  Without it a duplicate record
                  competes with itself and one copy loses.

    Returns the chosen column per object, -1 where it got none.  Ties go to
    the lower object index, then the lower column -- deterministic, so a re-run
    reproduces the file it wrote.
    """
    n_a, n_b = value.shape
    unit = _codes(units, n_b, "units")
    part = _codes(partition, n_a, "partition")
    ent = np.arange(n_a) if entities is None else _codes(entities, n_a, "entities")
    ii, jj = np.nonzero(allowed)
    order = np.lexsort((jj, ii, -value[ii, jj]))
    pick = np.full(n_a, -1, dtype=int)
    claimed: dict = {}
    for k in order:
        i, j = ii[k], jj[k]
        if pick[i] >= 0:
            continue
        key = (part[i], unit[j])
        if key in claimed and claimed[key] != ent[i]:
            continue
        claimed[key] = ent[i]
        pick[i] = j
    return pick


def select(scores: Scores, params: MatchParams = DEFAULTS, *,
           prefer=None,
           keys: Sequence | None = None,
           groups: Mapping[str, np.ndarray | None] | None = None,
           eligible: np.ndarray | None = None,
           units: Sequence | None = None,
           partition: Sequence | None = None,
           entities: Sequence | None = None) -> Selection:
    """Choose one counterpart per object and tally the admitted ones.

    ``prefer``    a boolean mask -- over side b, or over pairs -- whose rows
                  outrank every non-preferred admitted row whatever their
                  scores; or a *tuple* of such masks, highest priority first.
    ``keys``      side-b identifiers to count distinctly (a licence number, say).
    ``groups``    named boolean masks over side b; each yields a count column
                  and a key set per object.  A mask of ``None`` means "every
                  admitted row", which is how the plain match count is asked for.
    ``eligible``  a mask (side b or pairs) of what may be chosen; see the module
                  docstring.  Tallies ignore it.
    ``units``     side-b unit labels; turns on exclusivity (`greedy_assign`).
    ``partition`` side-a groups within which exclusivity holds.
    ``entities``  side-a entity labels; records of one entity share a unit.
    """
    ok = accepted(scores, params)
    n_a, n_b = ok.shape
    rows = np.arange(n_a)

    ranked = np.where(ok, np.nan_to_num(scores.score, nan=0.0), -1.0)
    if prefer is not None:
        levels = list(prefer) if isinstance(prefer, tuple) else [prefer]
        for k, m in enumerate(levels):
            lift = PREFER_LIFT * 2.0 ** (len(levels) - 1 - k)
            ranked = np.where(ok & _mask(m, n_a, n_b, "prefer"), ranked + lift, ranked)

    choosable = ok if eligible is None else ok & _mask(eligible, n_a, n_b, "eligible")
    has_choice = choosable.any(axis=1) if n_b else np.zeros(n_a, dtype=bool)
    best = (np.argmax(np.where(choosable, ranked, -np.inf), axis=1) if n_b
            else np.zeros(n_a, dtype=int))

    if units is not None and n_b:
        chosen = greedy_assign(ranked, choosable, units, partition, entities)
        hit = chosen >= 0
        pick = np.where(hit, chosen, best)
    else:
        pick = best
        hit = has_choice

    taken = has_choice & ~hit
    rival = np.full(n_a, -1, dtype=int)
    if taken.any():
        unit = _codes(units, n_b, "units")
        part = _codes(partition, n_a, "partition")
        owner = {(part[i], unit[pick[i]]): i for i in np.flatnonzero(hit)}
        for i in np.flatnonzero(taken):
            rival[i] = owner.get((part[i], unit[best[i]]), -1)
    blocked = (ok.any(axis=1) & ~has_choice) if n_b else np.zeros(n_a, dtype=bool)

    counts: dict[str, np.ndarray] = {}
    key_sets: dict[str, list[set]] = {}
    if groups:
        if keys is None:
            raise ValueError("groups need keys to count distinctly")
        key_arr = np.asarray(keys, dtype=object)
        for gname, mask in groups.items():
            m = ok if mask is None else (ok & np.asarray(mask, dtype=bool)[None, :])
            sets = [set(key_arr[np.flatnonzero(r)]) for r in m]
            key_sets[gname] = sets
            counts[gname] = np.array([len(s) for s in sets], dtype=int)

    methods = np.array(["", ROUTE_NAMES[0], ROUTE_NAMES[1]], dtype=object)
    name_kind = None
    if scores.name_kind is not None and n_b:
        kinds = np.array([NAME_KINDS[k] for k in sorted(NAME_KINDS)], dtype=object)
        name_kind = np.where(hit, kinds[scores.name_kind[rows, pick]], "")
    return Selection(
        accepted=ok, pick=pick, hit=hit,
        score=_gather(scores.score, rows, pick, hit),
        addr=_gather(scores.addr, rows, pick, hit),
        name=_gather(scores.name, rows, pick, hit),
        distance=_gather(scores.distance, rows, pick, hit),
        house=_gather(scores.house, rows, pick, hit),
        method=np.where(hit, methods[scores.method[rows, pick] + 1], ""),
        counts=counts, keys=key_sets, name_kind=name_kind,
        taken=taken, rival=rival, blocked=blocked,
    )


def claimed_keys(selection: Selection, group: str) -> set:
    """Every side-b key any object claimed in one group -- the reverse direction.

    "Which register rows did the export account for" is the question a coverage
    check asks, and it is not the complement of the forward one: one row can be
    claimed by many objects and many rows by none.
    """
    out: set = set()
    for s in selection.keys[group]:
        out |= s
    return out
