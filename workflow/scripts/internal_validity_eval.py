"""Internal-validity evaluation: how often the pipeline's match decisions are wrong.

Stage 2 of `internal_validity_sample.py`. Two blind LLM judges of different
models (judge 1: Sonnet, `judgments_{1..4}.jsonl`; judge 2: Opus,
`judgments2_{12,34}.jsonl`) labelled the 260 sampled decisions «object ↔
source record» from the packet alone. This script reweights their labels to
the population of decisions and reports, per source:

- the false-acceptance rate  P(judge: no  | pipeline accepted),
- the false-rejection rate   P(judge: yes | pipeline rejected a candidate),
- for the state strata (Yandex «Больше не работает», own inactive licence)
  the share where the judge sees another organisation or an open record.

The question is *internal*: given the pipeline's own sources, did it draw the
right conclusion. Whether the sources reflect the ground is §8 of the
methodology, not this.

Reference label: the two judges' answers where they agree. A yes/no
disagreement or any `unclear` is unresolved and enters as Manski bounds (all
unresolved against the pipeline / all in its favour); the point estimate on
resolved labels assumes unresolved-at-random within a stratum, which the
bounds exist to question. Each judge alone is reported as a sensitivity run.

Estimator: stratified ratio, weight N_h / n_h from the manifest (near misses
were over-sampled, so an unweighted share is wrong). 95 % CI: Wilson on the
Kish effective sample size of the resolved labels (the design effect of the
unequal weights), widened to the stratified bootstrap where the two differ by
more than 2 p.p. (the bootstrap alone is too narrow: a stratum whose labels are
all alike never varies under resampling). Fewer than 10 resolved
decisions: counts only. The analysis is exploratory: 7 sources x 2-3 rates,
no multiplicity correction.

Read-only over `results/finalest/internal_validity/`; writes `eval/` there.
Deterministic (seeded bootstrap). No network.

Usage: pixi run internal-validity
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from infracheck.audit.evaluate import cohen_kappa, wilson_interval

ROOT = Path(__file__).resolve().parents[2]
IV = ROOT / "results" / "finalest" / "internal_validity"
SEED = 20261122
N_BOOT = 4000
MIN_RESOLVED = 10
WIDEN_PP = 0.02

JUDGES = {
    "judge1": ("Судья 1 (Sonnet)", [f"judgments_{k}.jsonl" for k in (1, 2, 3, 4)]),
    "judge2": ("Судья 2 (Opus)", ["judgments2_12.jsonl", "judgments2_34.jsonl"]),
}
SAME = ("yes", "no", "unclear")
OPEN = ("open", "closed", "unclear")

SOURCES = ["yandex", "yandex_contractor", "merge", "relink", "fsq", "overture", "egais"]
SOURCE_RU = {
    "yandex": "Яндекс (наша проверка)",
    "yandex_contractor": "Яндекс подрядчика",
    "merge": "Склейка подрядчика OSM↔2GIS",
    "relink": "Relink (соседняя запись выгрузки)",
    "fsq": "Foursquare",
    "overture": "Overture",
    "egais": "ЕГАИС",
    "own": "Решения конвейера, все источники кроме склейки (взвешено по популяции решений)",
    "all": "Все решения, со склейкой подрядчика (взвешено по популяции решений)",
}
# Pooled domains.  The contractor's OSM+2GIS merge is a decision the pipeline
# takes on trust, not one it makes, so the pipeline's own pooled rate leaves it out.
POOLED = {"own": [s for s in SOURCES if s != "merge"], "all": SOURCES}
# What the pipeline did in each stratum.  "state": the record matched but its
# state (closed / inactive licence) kept it from counting.
ROLE = {
    "yandex_accepted": "accepted",
    "yandex_rejected_near": "rejected",
    "yandex_rejected_other": "rejected",
    "yandex_closed": "state",
    "yandex_contractor_accepted": "accepted",
    "yandex_contractor_rejected": "rejected",
    "merge_unlike_names": "accepted",
    "merge_alike_names": "accepted",
    "relink_accepted": "accepted",
    "relink_rejected": "rejected",
    "fsq_accepted": "accepted",
    "fsq_rejected_near": "rejected",
    "fsq_rejected_low": "rejected",
    "fsq_rejected_exclusive": "rejected",
    "overture_accepted": "accepted",
    "overture_rejected_near": "rejected",
    "overture_rejected_confidence": "rejected",
    "overture_rejected_low": "rejected",
    "overture_rejected_exclusive": "rejected",
    "egais_accepted": "accepted",
    "egais_rejected": "rejected",
    "egais_inactive": "state",
}
# Metric -> the role of the strata it is measured on.
METRICS = {
    "false_accept": "accepted",
    "false_reject": "rejected",
    "false_reject_open": "rejected",
    "state_error": "state",
}
METRIC_RU = {
    "false_accept": "Ложные принятия: P(нет | принято)",
    "false_reject": "Ложные отказы: P(да | отвергнут лучший кандидат)",
    "false_reject_open": "Ложные отказы с последствием: да и запись не закрыта",
    "state_error": "Ошибка состояния: другая организация или запись не закрыта",
}


# ----------------------------------------------------------------- labels


def error_label(metric: str, same_org: str, open_: str) -> float:
    """1 = the pipeline's decision is wrong by this judge, 0 = right, NaN = cannot tell.

    false_accept (accepted strata): wrong iff `no`.
    false_reject (rejected strata): wrong iff `yes`.
    false_reject_open: wrong iff `yes` and the record does not say closed — a
      rejected record the judge calls closed would not have counted anyway, so
      the rejection changed nothing.  (FSQ staleness is not modelled here.)
    state_error (matched, kept out as closed / inactive): right iff the same
      organisation *and* the record says closed; wrong iff another
      organisation or a record that says open.
    """
    if metric == "false_accept":
        return {"no": 1.0, "yes": 0.0}.get(same_org, np.nan)
    if metric == "false_reject":
        return {"yes": 1.0, "no": 0.0}.get(same_org, np.nan)
    if metric == "false_reject_open":
        if same_org == "yes":
            return 0.0 if open_ == "closed" else 1.0
        return 0.0 if same_org == "no" else np.nan
    if metric == "state_error":
        if same_org == "no" or open_ == "open":
            return 1.0
        if same_org == "yes" and open_ == "closed":
            return 0.0
        return np.nan
    raise ValueError(metric)


def consensus(a: pd.Series, b: pd.Series) -> pd.Series:
    """Agreeing resolved values; a disagreement or any NaN is unresolved."""
    return a.where(a.notna() & b.notna() & (a == b))


# ----------------------------------------------------------------- estimator


@dataclass
class Estimate:
    n: int  # sampled decisions in the domain
    resolved: int
    bad: int  # resolved errors (unweighted)
    unresolved_share: float  # weighted
    point: float
    lo95: float
    hi95: float
    bound_lo: float
    bound_hi: float
    n_eff: float
    sufficient: bool


def stratum_table(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Per stratum: N_h (population), n_h (sample), r_h (resolved), b_h (resolved errors)."""
    g = df.groupby("stratum", sort=True)
    return pd.DataFrame(
        {
            "N": g["pop_n"].first().astype(float),
            "n": g.size().astype(float),
            "r": g[col].apply(lambda s: s.notna().sum()).astype(float),
            "b": g[col].apply(lambda s: (s == 1).sum()).astype(float),
        }
    )


def point_estimate(t: pd.DataFrame) -> float:
    """Stratified ratio over strata with a resolved label (weights renormalised)."""
    t = t[t["r"] > 0]
    if t.empty:
        return np.nan
    return float((t["N"] * t["b"] / t["r"]).sum() / t["N"].sum())


def bounds(t: pd.DataFrame) -> tuple[float, float]:
    """Manski bounds: unresolved all in the pipeline's favour / all against it."""
    tot = t["N"].sum()
    lo = (t["N"] * t["b"] / t["n"]).sum() / tot
    hi = (t["N"] * (t["b"] + t["n"] - t["r"]) / t["n"]).sum() / tot
    return float(lo), float(hi)


def kish_neff(t: pd.DataFrame) -> float:
    t = t[t["r"] > 0]
    if t.empty:
        return 0.0
    w = t["N"] / t["r"]  # per resolved unit
    return float((w * t["r"]).sum() ** 2 / (w * w * t["r"]).sum())


def bootstrap_ci(df: pd.DataFrame, col: str, rng: np.random.Generator, n_boot: int = N_BOOT):
    """Percentile CI, resampling each stratum's sampled decisions with replacement."""
    groups = [(float(g["pop_n"].iloc[0]), g[col].to_numpy(float)) for _, g in df.groupby("stratum", sort=True)]
    est = np.full(n_boot, np.nan)
    for i in range(n_boot):
        num = den = 0.0
        for N, vals in groups:
            draw = vals[rng.integers(0, len(vals), len(vals))]
            res = draw[~np.isnan(draw)]
            if len(res):
                num += N * res.mean()
                den += N
        if den:
            est[i] = num / den
    est = est[~np.isnan(est)]
    if not len(est):
        return (np.nan, np.nan)
    return float(np.quantile(est, 0.025)), float(np.quantile(est, 0.975))


def estimate(df: pd.DataFrame, col: str, rng: np.random.Generator) -> Estimate:
    t = stratum_table(df, col)
    p = point_estimate(t)
    blo, bhi = bounds(t)
    resolved = int(t["r"].sum())
    unres = float((t["N"] * (t["n"] - t["r"]) / t["n"]).sum() / t["N"].sum())
    lo = hi = n_eff = np.nan
    if resolved:
        # Kish's effective size of the resolved labels: the weights run from 5 to
        # 1 157 per decision, so a stratum of 2 sampled decisions standing for
        # 2 315 rightly makes the interval wide.  It is never above the resolved
        # count and needs no within-stratum variance, which a stratum whose
        # labels are all alike (most of them here) would put at zero.
        n_eff = kish_neff(t)
        lo, hi = wilson_interval(p, p * (1 - p) / n_eff if 0 < p < 1 else 0.0, n_eff)
        blo_b, bhi_b = bootstrap_ci(df, col, rng)
        if np.isfinite(blo_b) and (abs(blo_b - lo) > WIDEN_PP or abs(bhi_b - hi) > WIDEN_PP):
            lo, hi = min(lo, blo_b), max(hi, bhi_b)
    return Estimate(
        n=len(df),
        resolved=resolved,
        bad=int(t["b"].sum()),
        unresolved_share=unres,
        point=p,
        lo95=lo,
        hi95=hi,
        bound_lo=blo,
        bound_hi=bhi,
        n_eff=n_eff,
        sufficient=resolved >= MIN_RESOLVED,
    )


# ----------------------------------------------------------------- loading


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load(iv: Path = IV) -> pd.DataFrame:
    manifest = json.loads((iv / "manifest.json").read_text(encoding="utf-8"))
    dec = pd.read_csv(iv / "decisions.csv", encoding="utf-8-sig", low_memory=False)
    if len(dec) != manifest["n_decisions"] or dec["decision_id"].duplicated().any():
        raise RuntimeError("decisions.csv does not match the manifest")
    unknown = set(dec["stratum"]) - set(ROLE)
    if unknown:
        raise RuntimeError(f"strata without a role: {sorted(unknown)}")
    # Weights come from the manifest, and decisions.csv must agree with it.
    strata = manifest["strata"]
    dec["pop_n"] = dec["stratum"].map(lambda s: strata[s]["population"])
    dec["design_n"] = dec["stratum"].map(lambda s: strata[s]["sample"])
    w = dec["pop_n"] / dec["design_n"]
    if not np.allclose(w.round(4), dec["weight"], atol=1e-4):
        raise RuntimeError("decisions.csv weights disagree with manifest population / sample")
    if (dec.groupby("stratum").size() != dec.groupby("stratum")["design_n"].first()).any():
        raise RuntimeError("sampled decisions per stratum disagree with the manifest")
    dec["role"] = dec["stratum"].map(ROLE)
    packets = {}
    for k in range(1, manifest["n_packets"] + 1):
        for p in read_jsonl(iv / f"packets_{k}.jsonl"):
            packets[p["decision_id"]] = p
    dec["object_name"] = dec["decision_id"].map(lambda i: packets[i]["object"]["name"])
    dec["object_address"] = dec["decision_id"].map(lambda i: packets[i]["object"]["address"])
    for key, (_, files) in JUDGES.items():
        rows = [r for f in files for r in read_jsonl(iv / f)]
        j = pd.DataFrame(rows)
        if j["decision_id"].duplicated().any() or set(j["decision_id"]) != set(dec["decision_id"]):
            raise RuntimeError(f"{key}: judgments do not cover each decision exactly once")
        if not j["same_org"].isin(SAME).all() or not j["open"].isin(OPEN).all():
            raise RuntimeError(f"{key}: label outside the prompt's vocabulary")
        j = j.set_index("decision_id")
        dec[f"{key}_same_org"] = dec["decision_id"].map(j["same_org"])
        dec[f"{key}_open"] = dec["decision_id"].map(j["open"])
        dec[f"{key}_reason"] = dec["decision_id"].map(j["reason"])
    for metric, role in METRICS.items():
        on = dec["role"].eq(role)
        for key in JUDGES:
            dec[f"err_{metric}_{key}"] = [
                error_label(metric, sv, ov) if o else np.nan
                for o, sv, ov in zip(on, dec[f"{key}_same_org"], dec[f"{key}_open"], strict=True)
            ]
        dec[f"err_{metric}_consensus"] = consensus(dec[f"err_{metric}_judge1"], dec[f"err_{metric}_judge2"])
    return dec


# ----------------------------------------------------------------- tables


REFERENCES = ("consensus", "judge1", "judge2")


def metrics(dec: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = []
    for ref in REFERENCES:
        for src in [*SOURCES, *POOLED]:
            for metric, role in METRICS.items():
                if src in POOLED and role == "state":
                    continue
                part = dec[dec["role"].eq(role) & dec["source"].isin(POOLED.get(src, [src]))]
                if part.empty:
                    continue
                e = estimate(part, f"err_{metric}_{ref}", rng)
                out.append(
                    {
                        "reference": ref,
                        "source": src,
                        "metric": metric,
                        "strata": ", ".join(sorted(part["stratum"].unique())),
                        "population": int(part.groupby("stratum")["pop_n"].first().sum()),
                        **e.__dict__,
                    }
                )
    m = pd.DataFrame(out)
    for c in ("point", "lo95", "hi95", "n_eff"):
        m.loc[~m["sufficient"], c] = np.nan
    return m


def strata_counts(dec: pd.DataFrame) -> pd.DataFrame:
    """Unweighted counts per stratum and reference: what each weighted rate rests on."""
    out = []
    for ref in REFERENCES:
        for metric, role in METRICS.items():
            part = dec[dec["role"].eq(role)]
            t = stratum_table(part, f"err_{metric}_{ref}").reset_index()
            t.insert(0, "metric", metric)
            t.insert(0, "reference", ref)
            t["weight"] = t["N"] / t["n"]
            out.append(t)
    return pd.concat(out, ignore_index=True).rename(
        columns={"N": "population", "n": "sample", "r": "resolved", "b": "errors"}
    )


def agreement(dec: pd.DataFrame) -> pd.DataFrame:
    out = []
    for src in [*SOURCES, "all"]:
        part = dec if src == "all" else dec[dec["source"].eq(src)]
        for q in ("same_org", "open"):
            a, b = part[f"judge1_{q}"], part[f"judge2_{q}"]
            both = a.isin(("yes", "no")) & b.isin(("yes", "no")) if q == "same_org" else None
            out.append(
                {
                    "source": src,
                    "question": q,
                    "n": len(part),
                    "agreement": float((a == b).mean()),
                    "kappa": float(cohen_kappa(a, b)),
                    "yes_no_disagreements": int((both & (a != b)).sum()) if both is not None else None,
                    "any_unclear": int((a.eq("unclear") | b.eq("unclear")).sum()),
                }
            )
    return pd.DataFrame(out)


def examples(dec: pd.DataFrame, per_source: int = 3) -> pd.DataFrame:
    """Errors by consensus first, then errors one judge alone calls (flagged)."""
    rows = []
    for src in SOURCES:
        part = dec[dec["source"].eq(src)].copy()
        main = np.where(
            part["role"].eq("accepted"),
            "false_accept",
            np.where(part["role"].eq("rejected"), "false_reject", "state_error"),
        )
        both = np.array([part.at[i, f"err_{m}_consensus"] == 1 for i, m in zip(part.index, main, strict=True)])
        one = np.array(
            [
                (part.at[i, f"err_{m}_judge1"] == 1) or (part.at[i, f"err_{m}_judge2"] == 1)
                for i, m in zip(part.index, main, strict=True)
            ]
        )
        part["kind"] = np.where(both, "оба судьи", np.where(one, "один судья", ""))
        part = part[part["kind"].ne("")]
        part["_o"] = part["kind"].eq("один судья").astype(int)
        part = part.sort_values(["_o", "role", "stratum", "rank_in_stratum"]).head(per_source)
        for r in part.itertuples():
            rows.append(
                {
                    "source": src,
                    "stratum": r.stratum,
                    "pipeline_decision": r.pipeline_decision,
                    "error_by": r.kind,
                    "decision_id": r.decision_id,
                    "city": r.city,
                    "object_name": r.object_name,
                    "candidate_name": r.candidate_name,
                    "distance_m": r.distance_m,
                    "judge1": f"{r.judge1_same_org}/{r.judge1_open}: {r.judge1_reason}",
                    "judge2": f"{r.judge2_same_org}/{r.judge2_open}: {r.judge2_reason}",
                }
            )
    return pd.DataFrame(rows)


def fmt(x: float, nd: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.{nd}f}".replace(".", ",")


def cell(r: pd.Series) -> str:
    """Methodology cell: point [CI]; bounds; counts — or counts only when insufficient."""
    counts = f"{r['bad']}/{r['resolved']} разреш. из {r['n']}"
    if not r["sufficient"]:
        return f"данных недостаточно ({counts})"
    return (
        f"{fmt(r['point'])} % [{fmt(r['lo95'])}; {fmt(r['hi95'])}]; "
        f"границы {fmt(r['bound_lo'])}–{fmt(r['bound_hi'])} %; {counts}"
    )


def summary_text(m: pd.DataFrame, ag: pd.DataFrame, ex: pd.DataFrame) -> str:
    lines = ["Internal validity: pipeline match decisions vs two blind LLM judges", ""]
    for ref in REFERENCES:
        lines.append(f"== reference: {ref}")
        for r in m[m["reference"].eq(ref)].itertuples():
            lines.append(f"  {r.source:18s} {r.metric:13s} {cell(pd.Series(r._asdict()))}")
        lines.append("")
    lines.append("== agreement (unweighted, sampled decisions)")
    for r in ag.itertuples():
        lines.append(f"  {r.source:18s} {r.question:8s} n={r.n:3d} agree={r.agreement:.3f} kappa={r.kappa:.3f}")
    lines.append("")
    lines.append("== examples")
    for r in ex.itertuples():
        lines.append(
            f"  [{r.source}/{r.stratum}/{r.error_by}] {r.object_name} <-> {r.candidate_name} ({r.distance_m} m)"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--iv", type=Path, default=IV)
    args = ap.parse_args(argv)
    dec = load(args.iv)
    m = metrics(dec)
    ag = agreement(dec)
    ex = examples(dec)
    st = strata_counts(dec)
    out = args.iv / "eval"
    out.mkdir(exist_ok=True)
    keep = [c for c in dec.columns if not c.startswith("_")]
    dec[keep].to_csv(out / "labels.csv", index=False, encoding="utf-8-sig")
    m.to_csv(out / "metrics.csv", index=False, encoding="utf-8-sig")
    ag.to_csv(out / "agreement.csv", index=False, encoding="utf-8-sig")
    st.to_csv(out / "strata.csv", index=False, encoding="utf-8-sig")
    ex.to_csv(out / "examples.csv", index=False, encoding="utf-8-sig")
    text = summary_text(m, ag, ex)
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
