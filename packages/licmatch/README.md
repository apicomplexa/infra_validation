# licmatch

Address-level record linkage between an object export and a licence register,
for Russian addresses.  Version **1.2.0**.

Two sources describe the same door in different words; one of them names a
licence *holder* rather than a *shop*; half of one side carries no usable
coordinate; and the result has to be defensible line by line rather than merely
plausible in aggregate. That is the whole problem this package solves.

The methodology — what the rule does, why each number is what it is, how it was
checked, and which readings of the result are forbidden — is
[docs/METHODOLOGY.md](docs/METHODOLOGY.md) (in Russian).

## Why it is a package

It was extracted from `src/arkhinfra/v4_egais.py`, where the rule and one
study's column names lived together. Split out, it gains three things:

* it is **testable without the study's data** — 159 unit tests on small
  hand-built fixtures;
* the study-specific half shrinks to adapters, output columns and the domain
  reading of a match, which is what a reviewer should be arguing about;
* it can be lifted into another project, or into a git submodule, unchanged.

Extraction was verified byte-for-byte: `pixi run match-egais-v4` writes exactly
the files it wrote before.

## Use

```python
import licmatch as lm

city = lm.home_city_re("Архангельск")

objects = lm.side(
    [lm.parse_address_line(v, city) for v in export["address"]],
    [lm.fascia_name(v) for v in export["name"]],
    export["lat"], export["lon"],
)
register = lm.side(
    [lm.parse_address_fields(s, h, city) for s, h in registry[["street", "house"]].values],
    [lm.holder_name(v) for v in registry["holder"]],
    registry["lat"], registry["lon"],
)

scores = lm.score_matrix(objects, register, lm.DEFAULTS)
sel = lm.select(scores, lm.DEFAULTS,
                prefer=registry["in_force"].to_numpy(bool),
                keys=registry["licence_no"],
                groups={"matched": None, "active": registry["in_force"].to_numpy(bool)})

sel.hit                      # matched at all
sel.take(registry["licence_no"])   # the chosen licence per object
sel.counts["active"]         # distinct licences in force at the address
sel.joined("matched")        # "L-1; L-2"
```

### New in 1.2: the whole match as one pure function

```python
res = lm.match(objects, register, policy)    # no files, no network, no globals
res.table        # one row per object, indexed like `objects`
res.brand        # the learned-brand result of an unblocked run (brands="learned")
```

`objects`: `name`, `address` (+ optional `address_2`), `lat`, `lon`, plus any
column the policy names.  `register` (RegisterTable): `record_id`, `holder_id`,
`holder_name`, `venue_name`, `address_line`, `street` / `house` / `settlement` /
`block`, `lat`, `lon`, `in_force`, `kind`.

Five stages, each a function of `licmatch.stages`: **candidates** (`prepare`)
→ **scoring** (`score`, brands learned here) → **admission** (`admit`: a list
of named conditions — `address`, `kind` / `kind_or_holder`, `in_force`,
`name`, `brand`, `archived`) → **assignment** (`assign`: one unit per object,
greedy, strongest claim first) → **explanation** (`explain`: a ladder of
near-miss rungs, and the conditions the best rejected candidate failed).

```python
policy = lm.Policy(
    home_cities=("Архангельск",),
    kinds=lm.Kinds(allowed=frozenset({"catering"}), wrong=frozenset({"retail"})),
    require_in_force=True,
    name_policy="required", name_fields=("holder", "venue"),
    unit=("street", "house", "venue"),
    exclusive=lm.Exclusive(share="name", fallback=False),
    explain=(lm.Rung(lm.OTHER_HOLDER, (lm.C_ADDRESS, lm.C_KIND, lm.C_IN_FORCE)),
             lm.Rung(lm.INACTIVE, (lm.C_ADDRESS, lm.C_KIND, lm.C_NAME))),
)
t = lm.match(objects, register, policy).table
t[["status", "record", "failed_rule", "score", "claimed_by"]]
```

Statuses are codes only — `own`, `inactive`, `taken`, `other_holder`,
`other_chain`, `wrong_kind`, `elsewhere`, `none`; the sentence a reader sees is
the adapter's.  The two archived joins (the v4 risk layers and the final
delivery's catering check) are two `Policy` values of this one function, and
reproduce their archived outputs cell for cell.  `lm.names_agree()` is the
word-level yes/no name rule the catering policy requires.  Methodology §19.

### New in 1.1

All four are opt-in or leave non-generic pairs exactly where 1.0 put them.

```python
# Generic fascias ("Продукты", a bare "Кафе") no longer lose the name term:
# it is dropped and its weight handed to the others.  On by default.
lm.MatchParams(neutral_generic=False)          # the 1.0 arithmetic

# Known (fascia, holder) pairs score name = 1.0; look them up by tax number.
register = lm.side(..., alias_keys=registry["inn"])
scores = lm.score_matrix(objects, register, lm.DEFAULTS, aliases=pairs)
scores.name_kind                                # NAME_FUZZY / NAME_ALIAS / NAME_GENERIC

# Ranked preferences, what may be chosen, and one outlet per object.
sel = lm.select(scores, lm.DEFAULTS,
                prefer=(in_force, retail),      # level 0 outranks level 1
                eligible=may_quote,             # (n_b,) or (n_a, n_b)
                units=registry["licence_no"],   # one object per outlet ...
                partition=export["layer"],      # ... within each layer
                entities=export["shop_id"])     # duplicate records share a unit
sel.taken, sel.rival, sel.blocked

# Learn local chains from where they stand (strength built without the name term).
res = lm.learn_brands(strength, objects["name_key"], registry["inn"],
                      sites=export["site_key"], units=registry["licence_no"])
res.table          # the evidence -- read it before using res.pairs
res.pairs          # accepted (fascia, holder) pairs for aliases=
res.cycle          # 0 converged, 2 two-round cycle, -1 hit max_iter
res.site_votes     # pair -> the sites that voted for it; with table["loo_accepted"]
                   # it says where a pair may not score its own name term
```

Why each exists, and what a learned pair may not be read as, is §10–§12 of
the methodology.

`side()` is the only interface between a source and the rule: whatever the
source's column names, dialect or file format, an adapter parses it into
`Toponym`s and name keys, and nothing downstream can tell the two sides apart.

## Layout

| file | what is in it |
| --- | --- |
| `text.py` | folding, light Russian stemming |
| `toponym.py` | address parsing, the `Toponym`, the address term |
| `names.py` | holder / fascia normalisation, the operator→fascia aliases, `brand_key`, generic fascias |
| `scoring.py` | `MatchParams`, the pair formula, the dense matrix, admission, name kinds |
| `select.py` | choice, ranked preference, eligibility, exclusive assignment, distinct-key tallies, reverse coverage |
| `brands.py` | `learn_brands`: (fascia, holder) pairs learned by voting across addresses |
| `agree.py` | `names_agree`: the word-level, two-script yes/no name rule |
| `stages.py` | `match()`, `Policy` and the five stages |
| `geo.py` | haversine |

## Install

The host repository vendors it: `arkhinfra.paths.ensure_licmatch_path()` puts
`packages/licmatch/src` on `sys.path`, so nothing needs installing and the
reports' byte-identical setup cell stays untouched.

To install it into an environment instead:

```
pip install -e packages/licmatch
```

Requires Python ≥ 3.11, `numpy`, `pandas`, `rapidfuzz`.

## Tests

```
pixi run licmatch-test                    # from the repository root
python packages/licmatch/run_tests.py     # anywhere
```

stdlib `unittest`, so the suite runs wherever the package is vendored. The test
that matters most is `test_scoring.py::TestMatrixMatchesFormula` (and its 1.1
twin `TestMatrixMatchesFormulaGenericAndAlias`): the dense matrix is an
optimisation of `pair_score()`, and an optimisation that quietly disagrees with
its definition is the defect class the suite exists to catch.
`test_exclusive.py` covers exclusivity, eligibility and ranked preferences;
`test_brands.py` runs brand learning on small synthetic cities;
`test_match.py` runs `match()` one condition, rung and assignment rule at a time.

## Turning it into a git submodule

The package is already self-contained — no import reaches outside it. To publish
it separately:

```
git subtree split --prefix=packages/licmatch -b licmatch-split
# push that branch to a new repository, then in the host repo:
git rm -r packages/licmatch
git submodule add <url> packages/licmatch
```

Nothing in the host repository has to change: `ensure_licmatch_path()` points at
the same path either way.
