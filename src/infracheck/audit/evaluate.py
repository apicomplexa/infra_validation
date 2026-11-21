"""Design-weighted evaluation of blind calibration audits."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from infracheck.audit.human_check import read_filled_workbook

YES_KINDS = {"да", "yes", "confirmed", "conf"}
NO_KINDS = {"нет", "no", "not", "rejected"}
UNCLEAR = "unclear"
REPORT_METRICS = {
    "P(E=1)": "Доля реальных объектов",
    "PPV existence": "Точность подтверждения",
    "NPV existence": "Отрицательная прогностическая ценность",
    "sensitivity existence": "Полнота подтверждения",
    "specificity existence": "Специфичность подтверждения",
    "category precision yes": "Точность вида «да»",
    "category precision no": "Точность вида «нет»",
    "kind coverage": "Покрытие оси вида",
    "wrong kind prevalence": "Доля объектов не своего вида",
    "closed among confirmed": "Доля закрытых среди подтверждённых",
    "confirmed vending": "Автоматы, подтверждённые аудитором как вендинг",
    "whole object correct": "Объект верен целиком",
}
REPORT_CATEGORIES = {"catering": "общепит", "alcohol": "алкоголь", "tobacco": "табак"}
REPORT_ANALYSES = {
    "main": "основной",
    "dependent_as_unclear": "зависимые метки как неясные",
    "without_yandex": "без опоры только на Яндекс",
}


def reference_exists(row):
    if row.get("exists") == "no" or row.get("open") == "closed":
        return 0
    if row.get("exists") == "yes":
        return 1
    return np.nan


def wilson_interval(estimate, variance, n_fallback, alpha=0.05):
    """Wilson interval using the protocol's effective sample size."""
    if not np.isfinite(estimate):
        return (np.nan, np.nan)
    n_eff = estimate * (1 - estimate) / variance if variance > 0 and 0 < estimate < 1 else max(float(n_fallback), 1.0)
    from statistics import NormalDist

    z = NormalDist().inv_cdf(1 - alpha / 2)
    denominator = 1 + z * z / n_eff
    center = (estimate + z * z / (2 * n_eff)) / denominator
    half = z * math.sqrt(estimate * (1 - estimate) / n_eff + z * z / (4 * n_eff**2)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def weighted_ratio(frame, numerator, denominator, strata=None, bootstrap=4000, seed=20261121, alpha=0.05):
    """Estimate ratio of HT totals, linearized variance, and stratified bootstrap CI."""
    data = frame.copy()
    data = data[data[denominator].notna() & data[numerator].notna()].copy()
    if data.empty:
        return {
            "estimate": np.nan,
            "low": np.nan,
            "high": np.nan,
            "bootstrap_low": np.nan,
            "bootstrap_high": np.nan,
            "n": 0,
        }
    strata = strata or ["delivery", "stratum"]
    counts = data.groupby(strata, dropna=False)["unit_id"].transform("size")
    data["analysis_weight"] = data.population_N / counts
    total_b = (data.analysis_weight * data[denominator]).sum()
    if total_b == 0:
        return {
            "estimate": np.nan,
            "low": np.nan,
            "high": np.nan,
            "bootstrap_low": np.nan,
            "bootstrap_high": np.nan,
            "n": 0,
        }
    estimate = (data.analysis_weight * data[numerator]).sum() / total_b
    variance = 0.0
    for _, group in data.groupby(strata, dropna=False):
        n = len(group)
        N = float(group.population_N.iloc[0])
        if n == 1 and N > 1:
            raise ValueError("Cannot estimate design variance from a singleton non-census stratum")
        if n >= N:
            continue
        z = (group[numerator] - estimate * group[denominator]) / total_b
        variance += N**2 * (1 - n / N) * z.var(ddof=1) / n
    low, high = wilson_interval(estimate, variance, len(data), 0.05)
    bonf_low, bonf_high = wilson_interval(estimate, variance, len(data), alpha)
    rng = np.random.default_rng(seed)
    reps = []
    groups = [g for _, g in data.groupby(strata, dropna=False)]
    for _ in range(bootstrap):
        nums = denoms = 0.0
        for group in groups:
            n = len(group)
            N = float(group.population_N.iloc[0])
            # Rao-Wu rescaling preserves the stratum's fixed sample size.
            draws = rng.choice(n, size=max(n - 1, 1), replace=True)
            counts = np.bincount(draws, minlength=n).astype(float)
            if n > 1:
                counts = 1 + math.sqrt(max(0, 1 - n / N)) * (n / (n - 1) * counts - 1)
            nums += np.sum(group.analysis_weight.to_numpy() * counts * group[numerator].to_numpy())
            denoms += np.sum(group.analysis_weight.to_numpy() * counts * group[denominator].to_numpy())
        if denoms:
            reps.append(nums / denoms)
    if reps:
        blo, bhi = np.quantile(reps, [0.025, 0.975])
        # Publish the wider interval if bootstrap and linearization differ materially.
        if max(abs(blo - low), abs(bhi - high)) > 0.02:
            low, high = min(low, blo), max(high, bhi)
    else:
        blo = bhi = np.nan
    return {
        "estimate": estimate,
        "low": low,
        "high": high,
        "bonferroni_low": bonf_low,
        "bonferroni_high": bonf_high,
        "bootstrap_low": blo,
        "bootstrap_high": bhi,
        "n": len(data),
    }


def add_reference(sample, labels):
    data = sample.merge(labels, on="unit_id", how="inner", suffixes=("", "_audit"), validate="one_to_one")
    data["E"] = data.apply(reference_exists, axis=1)
    data["identified"] = data.exists.eq("yes")
    data["K"] = np.where(data.identified, data.category_ok.map({"yes": 1, "no": 0}), np.nan)
    data["pipeline_exists"] = data.ver_exists.map({"conf": 1, "not": 0, "mixed": np.nan})
    data["pipeline_kind"] = data.ver_kind.map(
        lambda x: 1 if str(x).lower() in YES_KINDS else 0 if str(x).lower() in NO_KINDS else np.nan
    )
    return data


def make_weighted(sample, allocation):
    data = sample.merge(
        allocation[["delivery", "stratum", "N_h", "n_B_h"]], on=["delivery", "stratum"], validate="many_to_one"
    )
    data["population_N"] = data.N_h
    data["weight"] = data.N_h / data.n_B_h
    return data


def metric_rows(data, label="main", bootstrap=4000):
    if "category" not in data:
        raise ValueError("Sample must contain category for category-level estimates")
    if data.category.nunique() > 1:
        rows = []
        for category, part in data.groupby("category", sort=True):
            for row in metric_rows(part, label, bootstrap):
                row["category"] = category
                rows.append(row)
        return rows
    d = data.copy()
    specs = {
        "P(E=1)": ("E", "one"),
        "PPV existence": ("true_positive", "pipeline_exists"),
        "NPV existence": ("true_negative", "notpipeline"),
        "sensitivity existence": ("true_positive", "E"),
        "specificity existence": ("true_negative", "notE"),
        "category precision yes": ("kind_yes_correct", "pipeline_yes"),
        "category precision no": ("kind_no_correct", "pipeline_no"),
        "kind coverage": ("kind_decided", "one"),
        "wrong kind prevalence": ("notK", "one"),
        "closed among confirmed": ("closed_confirmed", "pipeline_exists"),
        "whole object correct": ("whole", "one"),
    }
    d["one"] = 1.0
    d["notE"] = 1 - d.E
    d["notpipeline"] = 1 - d.pipeline_exists
    d["true_positive"] = d.E * d.pipeline_exists
    d["true_negative"] = d.notE * d.notpipeline
    d["pipeline_yes"] = d.pipeline_kind.eq(1).astype(float)
    d["pipeline_no"] = d.pipeline_kind.eq(0).astype(float)
    d["notK"] = 1 - d.K
    d["kind_yes_correct"] = d.K * d.pipeline_yes
    d["kind_no_correct"] = d.notK * d.pipeline_no
    d["kind_decided"] = d.pipeline_kind.isin([0, 1]).astype(float)
    d["whole"] = np.where(d.E.eq(0) | d.K.eq(0), 0.0, np.where(d.E.eq(1) & d.K.eq(1), 1.0, np.nan))
    d["closed_confirmed"] = np.where(
        d.open.eq("unclear"), np.nan, d.open.eq("closed").astype(float) * d.pipeline_exists
    )
    if d.category.iloc[0] == "catering" and "is_vending" in d:
        d["pipeline_vending"] = d.ver_kind.eq("автомат").astype(float)
        d["vending_correct"] = np.where(
            d.is_vending.eq("unclear"), np.nan, d.is_vending.eq("yes").astype(float) * d.pipeline_vending
        )
        specs["confirmed vending"] = ("vending_correct", "pipeline_vending")
    # Bounds assign unresolved reference labels against or in favor of the pipeline.
    rows = []
    for name, (num, den) in specs.items():
        primary = name in {"P(E=1)", "PPV existence", "sensitivity existence", "category precision yes"}
        alpha = 0.05 / 12 if primary else 0.05
        result = weighted_ratio(d, num, den, bootstrap=bootstrap, alpha=alpha)
        if "existence" in name or name == "P(E=1)":
            unresolved = d.E.isna()
        elif name.startswith("category precision") or name == "wrong kind prevalence":
            unresolved = d.K.isna() & d.identified
        elif name == "confirmed vending":
            unresolved = d.is_vending.eq("unclear") & d.pipeline_vending.eq(1)
        elif name == "closed among confirmed":
            unresolved = d.open.eq("unclear") & d.pipeline_exists.eq(1)
        elif name == "whole object correct":
            unresolved = d.E.isna() | (d.K.isna() & d.E.eq(1))
        else:
            unresolved = pd.Series(False, index=d.index)
        for bound, value in (("low", 0), ("high", 1)):
            if unresolved.any():
                bounded = d.copy()
                assigned = (
                    1 - value if name in {"NPV existence", "category precision no", "wrong kind prevalence"} else value
                )
                if name in {"sensitivity existence", "specificity existence"}:
                    unknown = bounded.E.isna()
                    positive = bounded.pipeline_exists.eq(1)
                    assignment = (
                        np.where(positive, value, 1 - value)
                        if name == "sensitivity existence"
                        else np.where(positive, 1 - value, value)
                    )
                    bounded.loc[unknown, "E"] = assignment[unknown]
                elif name == "whole object correct":
                    bounded.loc[bounded.E.isna(), "E"] = assigned
                    if assigned == 0:
                        bounded.loc[bounded.E.eq(1) & bounded.K.isna(), "K"] = 0
                    else:
                        bounded.loc[bounded.E.eq(1) & bounded.K.isna(), "K"] = 1
                elif name.startswith("category precision") or name == "wrong kind prevalence":
                    bounded.loc[unresolved, "K"] = assigned
                elif name == "confirmed vending":
                    bounded.loc[unresolved, "vending_correct"] = value
                elif name == "closed among confirmed":
                    bounded.loc[unresolved, "closed_confirmed"] = value * bounded.loc[unresolved, "pipeline_exists"]
                else:
                    bounded.loc[unresolved, "E"] = assigned
                if "existence" in name or name == "P(E=1)":
                    bounded["notE"] = 1 - bounded.E
                    bounded["true_positive"] = bounded.E * bounded.pipeline_exists
                    bounded["true_negative"] = bounded.notE * bounded.notpipeline
                    bounded["whole"] = np.where(
                        bounded.E.eq(0) | bounded.K.eq(0), 0.0, np.where(bounded.E.eq(1) & bounded.K.eq(1), 1.0, np.nan)
                    )
                else:
                    bounded["notK"] = 1 - bounded.K
                    bounded["kind_yes_correct"] = bounded.K * bounded.pipeline_yes
                    bounded["kind_no_correct"] = bounded.notK * bounded.pipeline_no
                    bounded["whole"] = np.where(
                        bounded.E.eq(0) | bounded.K.eq(0), 0.0, np.where(bounded.E.eq(1) & bounded.K.eq(1), 1.0, np.nan)
                    )
                r = weighted_ratio(bounded, num, den, bootstrap=0, alpha=alpha)
                result[f"unclear_{bound}"] = r["estimate"]
            else:
                result[f"unclear_{bound}"] = result["estimate"]
        eligible = pd.Series(True, index=d.index)
        unclear_mask = d.E.isna() if "existence" in name or name == "P(E=1)" else d.K.isna()
        if name.startswith("category precision") or name == "wrong kind prevalence":
            eligible = d.identified
            unclear_mask = d.K.isna()
        elif name == "closed among confirmed":
            eligible = d.pipeline_exists.eq(1)
            unclear_mask = d.open.eq("unclear")
        elif name == "confirmed vending":
            eligible = d.pipeline_vending.eq(1)
            unclear_mask = d.is_vending.eq("unclear")
        elif name == "whole object correct":
            unclear_mask = d.whole.isna()
        elif name == "kind coverage":
            unclear_mask = pd.Series(False, index=d.index)
        weights = d.loc[eligible, "weight"]
        result["unclear_share"] = (
            float(weights[unclear_mask[eligible]].sum() / weights.sum()) if weights.sum() else np.nan
        )
        rows.append({"category": d.category.iloc[0], "metric": name, "analysis": label, **result})
    return rows


def confusion(data, row_col, col_col, row_values, col_values):
    out = []
    for r in row_values:
        for c in col_values:
            mask = data[col_col].isna() if c == "?" else data[col_col].eq(c)
            subset = data[data[row_col].eq(r) & mask]
            out.append({"pipeline": r, "auditor": c, "weighted_N": (subset.weight).sum(), "sample_n": len(subset)})
    return out


def evaluate(sample_path, allocation_path, labels_path, output_dir, human_path=None, bootstrap=4000):
    sample = pd.read_csv(sample_path).fillna({"ver_exists": "", "ver_kind": ""})
    allocation = pd.read_csv(allocation_path, sep="\t")
    labels = pd.read_csv(labels_path)
    for column in labels.select_dtypes(include=["object", "string"]).columns:
        labels[column] = labels[column].fillna("")
    missing_labels = set(sample.unit_id) - set(labels.unit_id)
    extra_labels = set(labels.unit_id) - set(sample.unit_id)
    if missing_labels or extra_labels:
        raise ValueError(f"Audit labels do not match sample: missing={len(missing_labels)}, extra={len(extra_labels)}")
    data = add_reference(make_weighted(sample, allocation), labels)
    reports = metric_rows(data, bootstrap=bootstrap)
    # Dependence sensitivity: flag records whose cited sources overlap pipeline src_* evidence.
    verified_types = (
        data.get("verified_url_types", pd.Series("", index=data.index))
        .astype(object)
        .fillna("")
        .astype(str)
        .str.lower()
    )
    source_cols = [c for c in data.columns if c.startswith("src_")]
    pipeline_sources = data.get("sources", pd.Series("", index=data.index)).astype(str).str.lower()

    def overlap_only(i, value):
        kinds = {v.strip() for v in value.split(";") if v.strip()}
        if not kinds:
            return False
        counted = {c[4:].lower() for c in source_cols if str(data.iloc[i][c]).lower() in {"true", "1"}}
        aliases = {"official_site": "official", "fsrar/egais": "egais", "2gis": "gis"}
        names = pipeline_sources.iloc[i]
        return all(aliases.get(kind, kind) in counted or aliases.get(kind, kind) in names for kind in kinds)

    dependent = pd.Series([overlap_only(i, val) for i, val in enumerate(verified_types)], index=data.index)
    data["dependent"] = dependent
    sensitive = data.copy()
    sensitive.loc[dependent, ["E", "K"]] = np.nan
    sensitive.loc[dependent, ["open", "is_vending"]] = "unclear"
    reports += metric_rows(sensitive, "dependent_as_unclear", bootstrap)
    no_yandex = data.copy()
    yandex = verified_types.map(lambda value: {v.strip() for v in value.split(";") if v.strip()} == {"yandex"})
    no_yandex.loc[yandex, ["E", "K"]] = np.nan
    no_yandex.loc[yandex, ["open", "is_vending"]] = "unclear"
    reports += metric_rows(no_yandex, "without_yandex", bootstrap)
    existence_rows = []
    kind_rows = []
    city_counts = []
    for category, part in data.groupby("category", sort=True):
        existence_rows.extend(
            {"category": category, **row} for row in confusion(part, "pipeline_exists", "E", [0, 1], [0.0, 1.0, "?"])
        )
        kind_rows.extend(
            {"category": category, **row}
            for row in confusion(
                part[part.identified],
                "ver_kind",
                "K",
                ["да", "нет", "автомат", "противоречие", "нет данных", "mixed"],
                [0.0, 1.0, "?"],
            )
        )
        for city, city_data in part.groupby("city", sort=True):
            city_counts.append(
                {
                    "category": category,
                    "city": city,
                    "sample_n": len(city_data),
                    "E_yes": int(city_data.E.eq(1).sum()),
                    "E_no": int(city_data.E.eq(0).sum()),
                    "E_unclear": int(city_data.E.isna().sum()),
                }
            )
            if city_data.E.notna().sum() >= 20:
                for row in metric_rows(city_data, f"city:{city}", bootstrap):
                    reports.append(row)
    threshold_rows = []
    if "n_sources_max" in data:
        for category, part in data.groupby("category", sort=True):
            for threshold in (1, 2, 3):
                simulated = part.copy()
                simulated["threshold_exists"] = (
                    pd.to_numeric(simulated.n_sources_max, errors="coerce").ge(threshold).astype(float)
                )
                simulated["tp_threshold"] = simulated.E * simulated.threshold_exists
                for metric_name, numerator, denominator in (
                    ("precision", "tp_threshold", "threshold_exists"),
                    ("recall", "tp_threshold", "E"),
                ):
                    estimate = weighted_ratio(
                        simulated.assign(**{numerator: simulated[numerator]}),
                        numerator,
                        denominator,
                        bootstrap=bootstrap,
                    )
                    threshold_rows.append(
                        {"category": category, "min_sources": threshold, "metric": metric_name, **estimate}
                    )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(reports).to_csv(out / "metrics.csv", index=False)
    pd.DataFrame(existence_rows).to_csv(out / "confusion_existence.csv", index=False)
    pd.DataFrame(kind_rows).to_csv(out / "confusion_kind.csv", index=False)
    pd.DataFrame(city_counts).to_csv(out / "city_counts.csv", index=False)
    pd.DataFrame(threshold_rows).to_csv(out / "source_thresholds.csv", index=False)
    text = [
        "# Оценка калибровки",
        "",
        f"Объектов B с метками аудитора: {len(data)}",
        "",
        "Первичные конечные точки приведены с 95% и 99,6% ДИ (поправка Бонферрони). "
        "Если доля неясных превышает 20%, заголовочный вывод — интервал неидентифицированности.",
        "",
        "| Категория | Метрика | Анализ | Оценка | 95% ДИ | 99,6% ДИ | Границы неясности |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in reports:
        category = row.get("category", "все")
        shown_category = REPORT_CATEGORIES.get(category, category)
        shown_metric = REPORT_METRICS.get(row["metric"], row["metric"])
        shown_analysis = REPORT_ANALYSES.get(row["analysis"], row["analysis"].replace("city:", "город: "))
        text.append(
            f"| {shown_category} | {shown_metric} | {shown_analysis} | {row['estimate']:.3f} "
            f"| [{row['low']:.3f}; {row['high']:.3f}] "
            f"| [{row.get('bonferroni_low', np.nan):.3f}; {row.get('bonferroni_high', np.nan):.3f}] "
            f"| [{row['unclear_low']:.3f}; {row['unclear_high']:.3f}] "
            f"({row['unclear_share']:.0%} неясных) |"
            if np.isfinite(row["estimate"])
            else f"| {shown_category} | {shown_metric} | {shown_analysis} | н/д | — | — | — |"
        )
    (out / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    if human_path and Path(human_path).exists():
        human_kappa(data, human_path, out)
    return data


def cohen_kappa(left, right, weights=None):
    left, right = list(left), list(right)
    if not left:
        return np.nan
    labels = sorted(set(left) | set(right))
    weights = np.ones(len(left)) if weights is None else np.asarray(weights, dtype=float)
    label_index = {label: index for index, label in enumerate(labels)}
    values = np.zeros((len(labels), len(labels)), dtype=float)
    np.add.at(
        values,
        ([label_index[value] for value in left], [label_index[value] for value in right]),
        weights,
    )
    observed = np.trace(values) / values.sum()
    expected = np.dot(values.sum(axis=0), values.sum(axis=1)) / values.sum() ** 2
    return (observed - expected) / (1 - expected) if expected < 1 else np.nan


def human_kappa(data, human_path, output):
    human = read_filled_workbook(human_path)
    joined = data.merge(human, on="unit_id", suffixes=("_auditor", "_human"))
    rows = []
    matrices = []
    joined["E_human"] = joined.apply(
        lambda row: (
            0 if row.exists_human == "no" or row.open_human == "closed" else 1 if row.exists_human == "yes" else ""
        ),
        axis=1,
    )
    joined["E_auditor"] = joined.apply(
        lambda row: (
            0
            if row.exists_auditor == "no" or row.open_auditor == "closed"
            else 1
            if row.exists_auditor == "yes"
            else ""
        ),
        axis=1,
    )
    joined["human_stratum"] = joined.E_auditor.map({1: "E=1", 0: "E=0"}).fillna("E=?")
    population_counts = data.E.map({1.0: "E=1", 0.0: "E=0"}).fillna("E=?").value_counts()
    selected_counts = joined.human_stratum.value_counts()
    joined["human_weight"] = joined.human_stratum.map(
        lambda key: population_counts.get(key, 0) / selected_counts.get(key, 1)
    )
    for axis in ("exists", "open", "category_ok", "E"):
        a, h = f"{axis}_auditor", f"{axis}_human"
        valid = joined[a].ne("") & joined[h].ne("")
        selected = joined.loc[valid]
        vals_a, vals_h = selected[a].tolist(), selected[h].tolist()
        weights = selected.human_weight.to_numpy()
        rng = np.random.default_rng(20261121)
        kappas, agreements = [], []
        resampling_groups = [
            np.flatnonzero(selected.human_stratum.to_numpy() == stratum) for stratum in selected.human_stratum.unique()
        ]
        for _ in range(4000):
            take = (
                np.concatenate([rng.choice(group, size=len(group), replace=True) for group in resampling_groups])
                if vals_a
                else []
            )
            if len(take):
                kappas.append(cohen_kappa([vals_a[i] for i in take], [vals_h[i] for i in take]))
                agreements.append(np.mean([vals_a[i] == vals_h[i] for i in take]))
        confusion = pd.crosstab(
            selected[a].astype(str),
            selected[h].astype(str),
            values=weights,
            aggfunc="sum",
        ).fillna(0)
        denominator = confusion.sum(axis=1).replace(0, np.nan)
        for auditor_value, values in confusion.div(denominator, axis=0).iterrows():
            for human_value, probability in values.items():
                matrices.append(
                    {"axis": axis, "auditor": auditor_value, "human": human_value, "probability": probability}
                )
        rows.append(
            {
                "axis": axis,
                "n": int(valid.sum()),
                "agreement": float(np.mean([x == y for x, y in zip(vals_a, vals_h, strict=False)]))
                if vals_a
                else np.nan,
                "agreement_low": float(np.nanquantile(agreements, 0.025)) if agreements else np.nan,
                "agreement_high": float(np.nanquantile(agreements, 0.975)) if agreements else np.nan,
                "kappa": cohen_kappa(vals_a, vals_h),
                "kappa_low": float(np.nanquantile(kappas, 0.025)) if kappas else np.nan,
                "kappa_high": float(np.nanquantile(kappas, 0.975)) if kappas else np.nan,
            }
        )
    pd.DataFrame(rows).to_csv(output / "human_kappa.csv", index=False)
    pd.DataFrame(matrices).to_csv(output / "human_confusion.csv", index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", choices=["A", "B"], default="B")
    parser.add_argument("--config", default="config/calibration.yaml")
    parser.add_argument("--bootstrap", type=int, default=4000)
    args = parser.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    output = Path(cfg["paths"]["output"])
    evaluate(
        output / f"sample_{args.sample}.csv",
        output / "allocation.tsv",
        output / f"auditor_labels_{args.sample}.csv",
        output,
        output / "human_check_B.xlsx",
        args.bootstrap,
    )


if __name__ == "__main__":
    main()
