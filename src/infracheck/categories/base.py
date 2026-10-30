"""The category rule: Evidence -> «Подтверждено», «Вид?», «Основания».

Every check writes rows of one shape (`core.evidence`); this module reads them
and passes the verdict, and it knows nothing about catering, alcohol or any
other class.  Everything that differs between categories -- which sources are
one source, how many are enough, the order of the clauses, the vocabulary that
tells a café from a shop -- is a block of `config/config.yaml`, parsed here
into a `Spec`.  A category module (`catering.py`, ...) only names its block.

Three questions, kept apart on purpose (docs/ARCHITECTURE.md, «Три оси»):

* **Existence.**  A row counts iff `identity` and `record_state` are in the
  counting set (strong, current) and its (source, origin) is not another
  group's record by the independence table.  Sources are counted once per
  independence group -- Yandex reached by the contractor and by our own query
  is one Yandex -- and «Подтверждено» is `n_groups >= min_sources`.  The
  export's own name never counts: it describes the object, it does not find it.
* **Kind.**  Each voting row's `kind_text` is classified with its scheme's
  vocabulary.  A ladder of regexes is read top down and the *order is the
  rule*: a vending machine is neither a café nor a shop, «кафедра» starts with
  «кафе», a «магазин-кафе» is catering.  The votes are folded as the archive's
  `profile()` did: one kind -> that kind, yes and no -> conflict, vending
  beside one other kind -> the other kind, nothing said -> no data.
* **Reasons.**  Counted clauses first, one per group, "LABEL: a; b"; then,
  after « || против/не найдено: », every row that was *not* counted and has
  something to say -- what was looked for, and what stood there instead.  The
  text must be byte-identical to the archive's, because people search it.

Ported from `arkhinfra.final_confirm` (`decide`, `profile`, `_kind`,
`_against`, `needs_yandex`, `summarise`; archive tag `archive-2026-10-30`).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

from infracheck.core import evidence

# Genitive of a small count, for a legend that says "не менее двух".
_GENITIVE = {1: "одного", 2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти"}


# ------------------------------------------------------------------------ spec

@dataclass(frozen=True)
class Group:
    """One independent source: every row of `sources` counts for it at most once.

    `checks` orders the group's clauses in «Основания» (Yandex: our own query
    before the contractor's); rows of checks not listed follow, in check order.
    """

    label: str
    column: str
    sources: tuple[str, ...]
    checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Exclusion:
    """Rows of `source` built only from `only` datasets: another group's record.

    An aggregator names the upstream datasets of a record in `origin`
    ("Foursquare|Overture"); the record is a new source only if some dataset
    other than `only` contributed.  Tokens are split on `sep`, case-folded,
    and the aggregator's own name (`ignore`) is not a dataset.  An eligible row
    that matches is dropped *silently*: it is not a finding at all, so it is
    neither counted nor listed after « || ».
    """

    source: str
    only: tuple[str, ...]
    ignore: tuple[str, ...] = ()
    sep: str = "|"

    def matches(self, origin: str) -> bool:
        tokens = {t.strip().casefold() for t in str(origin).split(self.sep)} - {""}
        tokens -= {t.casefold() for t in self.ignore}
        return bool(tokens) and tokens <= {t.casefold() for t in self.only}


@dataclass(frozen=True)
class Scheme:
    """How one vocabulary of `kind_text` is read.

    `ladder` -- (role, regex) pairs tried top down, first match wins; `lower`
    lowercases the text first.  `split` -- the text is a list of labels: `yes`
    if any label matches `any_yes`, `no` if every label's top level (before
    `top_sep`) is in `all_no_top`.
    """

    ladder: tuple[tuple[str, re.Pattern], ...] = ()
    lower: bool = False
    split: str = ""
    any_yes: re.Pattern | None = None
    all_no_top: tuple[str, ...] = ()
    top_sep: str = " > "


@dataclass(frozen=True)
class Vote:
    """One kind vote per object: rows of `scheme` (optionally from `checks`)."""

    scheme: str
    label: str = ""          # overrides the row's own label
    checks: tuple[str, ...] = ()
    keep_empty: bool = False  # vote even when kind_text is "" (the export's name)


@dataclass(frozen=True)
class Kind:
    column: str
    reasons_column: str
    vote_identity: tuple[str, ...]
    votes: tuple[Vote, ...]
    schemes: Mapping[str, Scheme]
    words: Mapping[str, str]   # role -> word: yes / no / vending / conflict / none
    unknown: str = "?"
    sep: str = " | "


@dataclass(frozen=True)
class Spec:
    """A category, as `config.yaml` describes it.  Built by `Spec.from_config`."""

    name: str
    checks: tuple[str, ...]
    min_sources: int
    count_identity: tuple[str, ...]
    count_state: tuple[str, ...]
    never_count: tuple[str, ...]
    groups: tuple[Group, ...]
    exclude: tuple[Exclusion, ...]
    pro_sep: str
    group_sep: str
    con_intro: str
    con_sep: str
    con_order: tuple[str, ...]
    no_sources: str
    sources_sep: str
    kind: Kind
    query_group: str = ""
    query_column: str = ""
    query_skip_checks: tuple[str, ...] = ()
    how_column: str = ""
    how_parts: tuple[Mapping, ...] = ()
    how_sep: str = "; "
    derived: Mapping[str, str] = field(default_factory=dict)
    summary: tuple[Mapping, ...] = ()
    combinations_title: str = ""
    combinations_empty: str = "—"
    raw: Mapping = field(default_factory=dict)

    @classmethod
    def from_config(cls, name: str, block: Mapping) -> Spec:
        ex, rs, kd = block["existence"], block["reasons"], block["kind"]
        schemes = {}
        for sname, s in kd["schemes"].items():
            schemes[sname] = Scheme(
                ladder=tuple((role, re.compile(rx)) for role, rx in s.get("ladder", [])),
                lower=bool(s.get("lower", False)), split=s.get("split", ""),
                any_yes=re.compile(s["any_yes"]) if s.get("any_yes") else None,
                all_no_top=tuple(s.get("all_no_top", [])), top_sep=s.get("top_sep", " > "))
        kind = Kind(
            column=kd["column"], reasons_column=kd["reasons_column"],
            vote_identity=tuple(kd["vote_identity"]),
            votes=tuple(Vote(scheme=v["scheme"], label=v.get("label", ""),
                             checks=tuple(v.get("checks", [])), keep_empty=bool(v.get("keep_empty", False)))
                        for v in kd["votes"]),
            schemes=schemes, words=dict(kd["words"]), unknown=kd.get("unknown", "?"),
            sep=kd.get("sep", " | "))
        q, how = block.get("query", {}), block.get("how", {})
        return cls(
            name=name, checks=tuple(block["checks"]),
            min_sources=int(ex["min_sources"]),
            count_identity=tuple(ex["count"]["identity"]),
            count_state=tuple(ex["count"]["record_state"]),
            never_count=tuple(ex.get("never_count", [])),
            groups=tuple(Group(label=g["label"], column=g["column"], sources=tuple(g["sources"]),
                               checks=tuple(g.get("checks", []))) for g in ex["groups"]),
            exclude=tuple(Exclusion(source=e["source"], only=tuple(e["only"]), ignore=tuple(e.get("ignore", [])),
                                    sep=e.get("sep", "|")) for e in ex.get("exclude", [])),
            sources_sep=ex.get("sources_sep", "; "),
            pro_sep=rs["pro_sep"], group_sep=rs["group_sep"], con_intro=rs["con_intro"],
            con_sep=rs["con_sep"], con_order=tuple(rs["con_order"]), no_sources=rs["no_sources"],
            kind=kind,
            query_group=q.get("group", ""), query_column=q.get("column", ""),
            query_skip_checks=tuple(q.get("skip_checks", [])),
            how_column=how.get("column", ""), how_parts=tuple(how.get("parts", [])),
            how_sep=how.get("sep", "; "),
            derived=dict(block.get("derived", {})),
            summary=tuple(block.get("summary", [])),
            combinations_title=block.get("combinations_title", ""),
            raw=block)

    def group_of(self, source: str) -> Group | None:
        return next((g for g in self.groups if source in g.sources), None)

    @property
    def min_sources_genitive(self) -> str:
        return _GENITIVE.get(self.min_sources, str(self.min_sources))


# ------------------------------------------------------------------- existence

def _excluded(ev: pd.DataFrame, spec: Spec) -> pd.Series:
    out = pd.Series(False, index=ev.index)
    for e in spec.exclude:
        out |= ev["source"].eq(e.source) & ev["origin"].map(e.matches).astype(bool)
    return out


def eligible(ev: pd.DataFrame, spec: Spec) -> pd.Series:
    """Rows whose axes say "this object, current", before the independence table."""
    grouped = ev["source"].map(lambda s: spec.group_of(s) is not None)
    return (ev["identity"].isin(spec.count_identity) & ev["record_state"].isin(spec.count_state)
            & ~ev["source"].isin(spec.never_count) & grouped)


def counted(ev: pd.DataFrame, spec: Spec) -> pd.Series:
    """Rows that count towards «Подтверждено» (eligible and not excluded)."""
    return eligible(ev, spec) & ~_excluded(ev, spec)


def existence(ev: pd.DataFrame, index: pd.Index, spec: Spec) -> pd.DataFrame:
    """Per object: one bool per group (`Group.column`), `n_sources`, `sources`, `confirmed`."""
    c = ev[counted(ev, spec)]
    out = pd.DataFrame(index=index)
    for g in spec.groups:
        out[g.column] = index.isin(c.loc[c["source"].isin(g.sources), "object_id"])
    cols = [g.column for g in spec.groups]
    out["n_sources"] = out[cols].astype(int).sum(axis=1).astype("int64")
    labels = [g.label for g in spec.groups]
    out["sources"] = [spec.sources_sep.join(lab for lab, on in zip(labels, row, strict=True) if on)
                      for row in out[cols].itertuples(index=False)]
    out["confirmed"] = (out["n_sources"] >= spec.min_sources).astype("int64")
    return out


def needs_query(ev: pd.DataFrame, index: pd.Index, spec: Spec) -> pd.Series:
    """Objects whose verdict the query source (our Yandex check) could still change.

    Fewer than `min_sources` groups *besides* the query's own group, and not
    already counted by one of `query_skip_checks` (the contractor's name-matched
    hit makes our query moot).  Computed from whatever evidence is passed, so
    the fetch step can call it before our own query has any rows.
    """
    c = ev[counted(ev, spec)]
    others = [g for g in spec.groups if g.label != spec.query_group]
    have = pd.Series(0, index=index, dtype="int64")
    for g in others:
        have += index.isin(c.loc[c["source"].isin(g.sources), "object_id"]).astype(int)
    skip = index.isin(c.loc[c["check"].isin(spec.query_skip_checks), "object_id"])
    return pd.Series((have.to_numpy() < spec.min_sources) & ~skip, index=index, name=spec.query_column)


# --------------------------------------------------------------------- reasons

def _check_rank(spec: Spec, order: tuple[str, ...]) -> dict[str, int]:
    """Listed checks first, in `order`; the rest after, in the category's check order."""
    rank = {c: i for i, c in enumerate(order)}
    for c in spec.checks:
        rank.setdefault(c, len(rank))
    return rank


def reasons(ev: pd.DataFrame, index: pd.Index, spec: Spec) -> pd.Series:
    """«Основания» per object.

    Counted rows: one clause per group in group order, "LABEL: a; b" with the
    group's label and its rows ordered by `Group.checks`.  Rows not counted
    that carry text: after `con_intro`, in `con_order` of checks, each as
    "ROW LABEL: text".  Excluded rows say nothing.  An object with no counted
    row reads `no_sources`.
    """
    ev = ev.assign(_pos=range(len(ev)))
    is_counted = counted(ev, spec)
    silent = eligible(ev, spec) & ~is_counted
    con_rank = _check_rank(spec, spec.con_order)
    pro_rows = ev[is_counted]
    con_rows = ev[~is_counted & ~silent & ev["text"].ne("")]
    con_rows = con_rows.assign(_rank=con_rows["check"].map(lambda c: con_rank.get(c, len(con_rank))))
    con_rows = con_rows.sort_values(["_rank", "_pos"], kind="stable")
    pro_by = {k: d for k, d in pro_rows.groupby("object_id", sort=False)}
    con_by = {k: d for k, d in con_rows.groupby("object_id", sort=False)}
    out = []
    for oid in index:
        parts = []
        d = pro_by.get(oid)
        if d is not None:
            for g in spec.groups:
                gd = d[d["source"].isin(g.sources)]
                if gd.empty:
                    continue
                rank = _check_rank(spec, g.checks)
                gd = gd.assign(_rank=gd["check"].map(lambda c, r=rank: r.get(c, len(r))))
                gd = gd.sort_values(["_rank", "_pos"], kind="stable")
                parts.append(f"{g.label}: " + spec.group_sep.join(gd["text"]))
        text = spec.pro_sep.join(parts) or spec.no_sources
        d = con_by.get(oid)
        if d is not None and len(d):
            text += spec.con_intro + spec.con_sep.join(f"{r.label}: {r.text}" for r in d.itertuples())
        out.append(text)
    return pd.Series(out, index=index, name="reasons", dtype=object)


# ------------------------------------------------------------------------ kind

def classify(text: str, scheme: Scheme) -> str:
    """The role ('yes' / 'no' / 'vending') one text plays in its scheme, or ''."""
    if not text:
        return ""
    if scheme.split:
        labels = text.split(scheme.split)
        if scheme.any_yes is not None and any(scheme.any_yes.search(lab) for lab in labels):
            return "yes"
        if scheme.all_no_top and all(lab.split(scheme.top_sep)[0] in scheme.all_no_top for lab in labels):
            return "no"
        return ""
    t = text.lower() if scheme.lower else text
    return next((role for role, rx in scheme.ladder if rx.search(t)), "")


def fold_votes(roles: list[str]) -> str:
    """The archive's fold: one kind, conflict (yes + no), or vending beside one other."""
    said = {r for r in roles if r}
    if not said:
        return "none"
    if len(said) == 1:
        return next(iter(said))
    if "yes" in said and "no" in said:
        return "conflict"
    return (said - {"vending"}).pop()


def kind(ev: pd.DataFrame, index: pd.Index, spec: Spec) -> pd.DataFrame:
    """«Вид?» and its reasons: the verdict word and "LABEL: «text» → word" per vote."""
    k = spec.kind
    ev = ev[ev["identity"].isin(k.vote_identity)]
    by_obj = {o: d for o, d in ev.groupby("object_id", sort=False)}
    verdicts, texts = [], []
    for oid in index:
        d = by_obj.get(oid)
        votes = []
        if d is not None:
            for v in k.votes:
                vd = d[d["kind_scheme"].eq(v.scheme)]
                if v.checks:
                    vd = vd[vd["check"].isin(v.checks)]
                for r in vd.itertuples():
                    if not r.kind_text and not v.keep_empty:
                        continue
                    role = classify(r.kind_text, k.schemes[v.scheme])
                    votes.append((v.label or r.label, r.kind_text, role))
        verdicts.append(k.words[fold_votes([role for _, _, role in votes])])
        texts.append(k.sep.join(f"{lab}: «{txt}» → {k.words.get(role) if role else k.unknown}"
                                for lab, txt, role in votes))
    return pd.DataFrame({k.column: verdicts, k.reasons_column: texts}, index=index)


# ------------------------------------------------------------------------- how

def how(ev: pd.DataFrame, index: pd.Index, spec: Spec) -> pd.Series:
    """A short "how was this source reached" text, e.g. «Яндекс: как засчитан».

    Each part names a check and the row field whose value picks the text
    (`identity` for the contractor's hit, the check's own status column for our
    query); parts with no row or an unmapped value are skipped.
    """
    by_check = {p["check"]: ev[ev["check"].eq(p["check"])].drop_duplicates("object_id").set_index("object_id")
                for p in spec.how_parts}
    out = []
    for oid in index:
        parts = []
        for p in spec.how_parts:
            e = by_check[p["check"]]
            if oid in e.index:
                parts.append(p["texts"].get(str(e.at[oid, p["key"]]), ""))
        out.append(spec.how_sep.join(filter(None, parts)))
    return pd.Series(out, index=index, name=spec.how_column, dtype=object)


# ---------------------------------------------------------------------- decide

def _expr(table: pd.DataFrame, expr: str, spec: Spec):
    """A config expression over the table's columns (plus `min_sources`, bool, int, str)."""
    ns = {c: table[c] for c in table.columns}
    ns["min_sources"] = spec.min_sources
    return eval(expr, {"__builtins__": {"bool": bool, "int": int, "str": str}}, ns)  # noqa: S307 -- trusted config


def decide(objects: pd.DataFrame, ev: pd.DataFrame, wide: pd.DataFrame, spec: Spec) -> pd.DataFrame:
    """The object table of one delivery with the verdict.

    `objects` is the ObjectTable, `ev` every check's Evidence stacked in
    `spec.checks` order, `wide` the checks' own columns (each module's
    `wide()`), indexed by object_id.  Returns the ObjectTable joined with
    `wide`, the kind columns, one bool per group, `n_sources`, `sources`,
    `confirmed`, the `how` text, `reasons`, the query flag and the `derived`
    expressions -- the archive's `final_confirm_objects.csv`, row for row.
    Where a check's wide column has the name of an adapter column (the
    export's `osm` / `gis` flags), the check's -- rebuilt from Evidence -- wins.
    """
    evidence.validate(ev)
    index = objects.index
    out = objects.drop(columns=[c for c in wide.columns if c in objects.columns]).join(wide)
    out = out.join(kind(ev, index, spec))
    out = out.join(existence(ev, index, spec))
    for name, expr in spec.derived.items():
        out[name] = _expr(out, expr, spec)
    if spec.how_column:
        out[spec.how_column] = how(ev, index, spec)
    out["reasons"] = reasons(ev, index, spec)
    if spec.query_column:
        out[spec.query_column] = needs_query(ev, index, spec)
    return out


# --------------------------------------------------------------------- summary

def _value(line: Mapping, table: pd.DataFrame, rows: pd.DataFrame, spec: Spec):
    v = line.get("value")
    if v == "rows":
        return len(rows)
    if v == "objects":
        return len(table)
    if v == "confirmed_objects":
        return f"{table['confirmed'].sum()} из {len(table)} ({table['confirmed'].mean():.1%})"
    if v == "confirmed_rows":
        return f"{rows['confirmed'].sum()} из {len(rows)}"
    if "count" in line:
        return int(pd.Series(_expr(table, line["count"], spec)).astype(bool).sum())
    if "counts" in line:
        return line.get("sep", " / ").join(
            str(int(pd.Series(_expr(table, e, spec)).astype(bool).sum())) for e in line["counts"])
    raise ValueError(f"summary line without a value: {line}")


def summarise(table: pd.DataFrame, rows: pd.DataFrame, spec: Spec) -> list[tuple[str, object]]:
    """The few numbers a reader of one file needs, as (label, value) pairs.

    Lines come from `summary` in the category's config: a fixed `value`
    (rows / objects / confirmed_objects / confirmed_rows), a `count` -- a
    boolean expression over the decided table's columns -- or `counts`, several
    joined by " / ".  A line with `for` repeats once per item, `{}` in label
    and expression replaced by it.  `rows` is the table broadcast to the
    delivery's rows (`confirmed` per row).
    """
    out = []
    for line in spec.summary:
        items = line.get("for")
        if items is None:
            out.append((line["label"], _value(line, table, rows, spec)))
            continue
        for it in items:
            one = {k: (v.replace("{}", str(it)) if isinstance(v, str) else v)
                   for k, v in line.items() if k != "for"}
            out.append((one["label"], _value(one, table, rows, spec)))
    return out


def summary_text(title: str, summary: list[tuple[str, object]], table: pd.DataFrame, spec: Spec) -> str:
    """One delivery's block of the combined summary file, as the archive wrote it."""
    combos = table["sources"].replace("", spec.combinations_empty).value_counts().to_string()
    return (f"== {title}\n" + "\n".join(f"{k}: {v}" for k, v in summary)
            + f"\n\n{spec.combinations_title}\n" + combos + "\n")


def combined_summary(blocks: list[str]) -> str:
    return "\n".join(blocks)


def to_rows(raw: pd.DataFrame, rowmap: pd.DataFrame, table: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Broadcast the object verdict back onto every row of the delivery.

    `raw` and `rowmap` are row-aligned (the input adapter guarantees it);
    `columns` are the decided table's columns to carry; one the rows already
    have (the adapter's `entity`) is kept as the rows have it.
    """
    if len(raw) != len(rowmap):
        raise RuntimeError(f"{len(raw)} rows but {len(rowmap)} in the row map")
    oid = rowmap["object_id"].to_numpy()
    cols = [c for c in dict.fromkeys(columns) if c not in raw.columns]
    return raw.assign(_object_id=oid).join(table[cols], on="_object_id").drop(columns="_object_id")
