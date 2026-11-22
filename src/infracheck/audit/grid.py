"""Offline calibration grid, using the sealed A units and A design weights."""

from __future__ import annotations

import argparse
import copy
import itertools
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from infracheck.audit import evaluate
from infracheck.audit.evaluate import variance_groups
from infracheck.audit.grid_runner import run_point

DIMENSIONS = {
    "yandex_name": (65, 72, 80),
    "places_name": (80, 85, 90),
    "fresh": ("2020-01-01", "2022-01-01", "2024-01-01"),
    "radius": ((100, 40), (150, 60)),
}


def current_values(category):
    p = category["policy"]
    return (
        p["yandex"]["name_min"],
        p["overture"]["name_min"],
        str(p["foursquare"]["fresh"]),
        (p["yandex"]["near_m"], p["yandex"]["generic_m"]),
    )


def override_category(category, values):
    out = copy.deepcopy(category)
    p = out["policy"]
    p["yandex"]["name_min"] = values[0]
    for check in ("overture", "foursquare"):
        p[check]["name_min"] = values[1]
    p["foursquare"]["fresh"] = values[2]
    for check in ("yandex", "overture", "foursquare"):
        p[check]["near_m"], p[check]["generic_m"] = values[3]
    return out


def grid_points(category):
    current = current_values(category)
    axes = list(DIMENSIONS.values())
    candidates = (
        list(itertools.product(*axes))
        if math.prod(map(len, axes)) <= 81
        else [current]
        + [tuple(v if i == j else current[i] for i in range(len(axes))) for j, axis in enumerate(axes) for v in axis]
    )
    candidates.append(current)
    return sorted(
        set(p for p in candidates if sum(a != b for a, b in zip(p, current, strict=False)) <= 3),
        key=lambda p: (sum(a != b for a, b in zip(p, current, strict=False)), str(p)),
    )


def scores(data, allocation, bootstrap=1000, seed=20261121):
    """Ratios retain N_h/n_A,h; bootstrap is stratified and FPC-rescaled."""
    d, alloc = variance_groups(data.reset_index(drop=True), allocation)
    groups = []
    for row in alloc.itertuples():
        g = d[(d.delivery == row.delivery) & (d.variance_stratum == row.variance_stratum)]
        if len(g):
            groups.append((g.index.to_numpy(), row.N_h, row.n_A_h))
    w = d.weight.to_numpy(float)
    e = d.E.to_numpy(float)
    k = d.K.to_numpy(float)
    pred = d.pipeline_exists.to_numpy(float)
    pk = d.pipeline_kind.to_numpy(float)

    def calculate(weights, missing=None, kind_missing=None):
        ref = e.copy()
        if missing is not None:
            unknown = np.isnan(ref)
            ref[unknown] = pred[unknown] if missing else 1 - pred[unknown]
        known = np.isfinite(ref) & np.isfinite(pred)

        def ratio(num, den):
            mask = np.isfinite(num) & np.isfinite(den)
            adjusted = weights.copy()
            for _, idx in d.groupby(["delivery", "stratum"]).groups.items():
                idx = np.asarray(list(idx), dtype=int)
                available = idx[mask[idx]]
                if len(available) and weights[available].sum() > 0:
                    adjusted[available] *= weights[idx].sum() / weights[available].sum()
            denominator = np.sum(adjusted[mask] * den[mask])
            return np.sum(adjusted[mask] * num[mask]) / denominator if denominator else np.nan

        tp = np.where(known, ref * pred, np.nan)
        tn = np.where(known, (1 - ref) * (1 - pred), np.nan)
        recall = ratio(tp, np.where(known, ref, np.nan))
        specificity = ratio(tn, np.where(known, 1 - ref, np.nan))
        kind_ref = k.copy()
        if kind_missing is not None:
            unknown_kind = np.isnan(kind_ref) & d.identified.to_numpy(bool)
            kind_ref[unknown_kind] = float(kind_missing)
        kind = np.isfinite(kind_ref) & np.isfinite(pk)
        return np.array(
            [
                ratio(tp, np.where(known, pred, np.nan)),
                recall,
                ratio(tp + tn, np.where(known, 1.0, np.nan)),
                ratio(np.where(kind, kind_ref * pk, np.nan), np.where(kind, pk, np.nan)),
                ratio(np.isfinite(pk).astype(float), np.ones(len(pk))),
                (recall + specificity) / 2,
            ]
        )

    point = calculate(w)

    def influence(num, den):
        mask = np.isfinite(num) & np.isfinite(den)
        adjusted = w.copy()
        for _, idx in d.groupby(["delivery", "stratum"]).groups.items():
            idx = np.asarray(list(idx), dtype=int)
            available = idx[mask[idx]]
            if len(available):
                adjusted[available] *= w[idx].sum() / w[available].sum()
        total = np.sum(adjusted[mask] * den[mask])
        if not total:
            return np.full(len(d), np.nan)
        estimate = np.sum(adjusted[mask] * num[mask]) / total
        return np.where(mask, adjusted * (np.nan_to_num(num) - estimate * np.nan_to_num(den)) / total, 0.0)

    known = np.isfinite(e) & np.isfinite(pred)
    tp = np.where(known, e * pred, np.nan)
    tn = np.where(known, (1 - e) * (1 - pred), np.nan)
    kp_known = np.isfinite(k) & np.isfinite(pk)
    recall_if = influence(tp, np.where(known, e, np.nan))
    spec_if = influence(tn, np.where(known, 1 - e, np.nan))
    influences = [
        influence(tp, np.where(known, pred, np.nan)),
        recall_if,
        influence(tp + tn, np.where(known, 1.0, np.nan)),
        influence(np.where(kp_known, k * pk, np.nan), np.where(kp_known, pk, np.nan)),
        influence(np.isfinite(pk).astype(float), np.ones(len(pk))),
        (recall_if + spec_if) / 2,
    ]

    # Missing completed labels do not become synthetic observations. Incomplete strata
    # cannot provide valid design intervals; point estimates remain descriptive.
    complete = len(d) == int(allocation.n_A_h.sum())
    reps = []
    rng = np.random.default_rng(seed)
    if complete:
        for _ in range(bootstrap):
            bw = w.copy()
            for index, N, n in groups:
                if n >= N:
                    continue
                if len(index) < 2:
                    complete = False
                    break
                draws = rng.choice(len(index), size=len(index) - 1, replace=True)
                counts = np.bincount(draws, minlength=len(index))
                bw[index] *= 1 + math.sqrt(1 - n / N) * (len(index) / (len(index) - 1) * counts - 1)
            if not complete:
                break
            reps.append(calculate(bw))
    names = ["precision", "recall", "accuracy", "kind_precision", "kind_coverage", "balanced_accuracy"]
    result = {}
    result["existence_unclear_share"] = float(w[np.isnan(e)].sum() / w.sum()) if w.sum() else np.nan
    identified = d.identified.to_numpy(bool)
    identified_weight = w[identified].sum()
    result["kind_unclear_share"] = (
        float(w[identified & np.isnan(k)].sum() / identified_weight) if identified_weight else np.nan
    )
    for i, name in enumerate(names):
        vals = np.array(reps)[:, i] if reps else np.array([])
        vals = vals[np.isfinite(vals)]
        result[name] = float(point[i])
        result[name + "_bootstrap_low"] = float(np.quantile(vals, 0.025)) if len(vals) else np.nan
        result[name + "_bootstrap_high"] = float(np.quantile(vals, 0.975)) if len(vals) else np.nan
        variance = 0.0
        if complete and np.isfinite(influences[i]).all():
            for index, N, n in groups:
                if n >= N:
                    continue
                z = influences[i][index]
                if len(z) < 2:
                    variance = np.nan
                    break
                variance += (1 - n / N) * len(z) / (len(z) - 1) * np.sum((z - z.mean()) ** 2)
        else:
            variance = np.nan
        result[name + "_se"] = float(np.sqrt(variance))
        low, high = evaluate.wilson_interval(point[i], variance, len(d)) if np.isfinite(variance) else (np.nan, np.nan)
        result[name + "_low"], result[name + "_high"] = low, high
        if name in {"precision", "recall", "accuracy"}:
            # Assign unclear existence labels both ways; recall uses opposing assignments
            # for predicted positives and negatives to attain its extrema.
            result[name + "_unclear_low"] = float(calculate(w, False)[i])
            result[name + "_unclear_high"] = float(calculate(w, True)[i])
    result["kind_precision_unclear_low"] = float(calculate(w, kind_missing=False)[3])
    result["kind_precision_unclear_high"] = float(calculate(w, kind_missing=True)[3])
    return result


def select_point(rows, current_id, objective="balanced_accuracy", stability="stable"):
    """One-SE rule, stop threshold, coverage and leave-one-city-out stability."""
    current = next(r for r in rows if r["point_id"] == current_id)
    eligible = [
        r
        for r in rows
        if np.isfinite(r.get(objective, np.nan))
        and np.isfinite(r.get(objective + "_se", np.nan))
        and np.isfinite(r["kind_coverage"])
        and r["kind_coverage"] >= current["kind_coverage"]
    ]
    if not eligible or not np.isfinite(current.get(objective, np.nan)):
        return current, "Недостаточно размеченных данных для выбора."
    best = max(eligible, key=lambda r: r[objective])
    gain = best[objective] - current[objective]
    if gain < 0.03 or gain < best[objective + "_se"]:
        return current, "Выигрыш лучшей конфигурации меньше 3 п. п. или одной стандартной ошибки."
    within = [r for r in eligible if r[objective] >= best[objective] - best[objective + "_se"]]
    chosen = min(within, key=lambda r: (r["distance"], r["point_id"] != current_id, r["point_id"]))
    if chosen["point_id"] == current_id:
        return current, "Текущая конфигурация входит в интервал одной стандартной ошибки."
    if not chosen.get(stability, False):
        return current, "Выигрыш неустойчив при исключении города."
    return chosen, "Правило одной стандартной ошибки; выигрыш устойчив при исключении каждого города."


def read_labels(path, sample):
    if not path.exists() or not path.stat().st_size:
        return pd.DataFrame(columns=["unit_id", "exists", "open", "category_ok", "is_vending"])
    labels = pd.read_csv(path).fillna("")
    required = {"unit_id", "exists", "open", "category_ok", "is_vending"}
    if not required <= set(labels):
        raise ValueError(f"Missing label columns: {sorted(required - set(labels))}")
    if labels.unit_id.duplicated().any() or not set(labels.unit_id) <= set(sample.unit_id):
        raise ValueError("Duplicate or unknown unit_id in labels")
    for col, valid in {
        "exists": {"yes", "no", "unclear"},
        "open": {"open", "closed", "unclear"},
        "category_ok": {"yes", "no", "unclear"},
        "is_vending": {"yes", "no", "unclear"},
    }.items():
        if not labels[col].isin(valid).all():
            raise ValueError(f"Invalid or incomplete label: {col}")
    return labels


def run(root=Path("."), bootstrap=1000):
    root = Path(root)
    config = yaml.safe_load((root / "config/config.yaml").read_text())
    sample = pd.read_csv(root / "results/finalest/calibration/sample_A.csv").fillna("")
    alloc = pd.read_csv(root / "results/finalest/calibration/allocation.tsv", sep="\t")
    labels = read_labels(root / "results/finalest/calibration/auditor_labels_A.csv", sample)
    out = root / "results/finalest/calibration/grid"
    out.mkdir(parents=True, exist_ok=True)
    recommendations = {}
    for category_name, part in sample.groupby("category", sort=True):
        category = config["categories"][category_name]
        current = current_values(category)
        points = grid_points(category)
        category_alloc = alloc[alloc.delivery.isin(part.delivery.unique())].copy()
        category_labels = labels[labels.unit_id.isin(part.unit_id)]
        print(
            f"{category_name}: {len(points)} конфигураций, размечено {len(category_labels)} из {len(part)}", flush=True
        )
        rows, predictions, data_by_point = [], [], {}
        for number, values in enumerate(points):
            point_id = f"{category_name}_{number:03d}"
            verdicts = run_point(config, category_name, override_category(category, values), part, root=root)
            verdicts = verdicts.copy()
            verdicts["point_id"] = point_id
            predictions.append(verdicts)
            d = part.drop(columns=["ver_exists", "ver_kind"]).merge(verdicts, on="unit_id", validate="one_to_one")
            d["ver_exists"] = d.confirmed_object.astype(int).map({1: "conf", 0: "not"})
            d = d.merge(
                category_alloc[["delivery", "stratum", "N_h", "n_A_h"]],
                on=["delivery", "stratum"],
                validate="many_to_one",
            )
            d["weight"] = d.N_h / d.n_A_h
            d["population_N"] = d.N_h
            d = evaluate.add_reference(d, category_labels)
            data_by_point[point_id] = d
            row = {
                "point_id": point_id,
                "values": values,
                "distance": sum(a != b for a, b in zip(values, current, strict=False)),
                "labelled": len(d),
                "sample_n": len(part),
            }
            if len(d):
                row.update(scores(d, category_alloc, bootstrap=bootstrap))
            else:
                row.update({k: np.nan for k in ("balanced_accuracy", "balanced_accuracy_se", "kind_coverage")})
            rows.append(row)
        current_id = rows[0]["point_id"]
        cities = sorted(part.city.unique())
        base = data_by_point[current_id]
        for row in rows:
            differences = []
            kind_differences = []
            for city in cities:
                a = category_alloc[~category_alloc.delivery.isin(part.loc[part.city == city, "delivery"])]
                test = data_by_point[row["point_id"]].query("city != @city")
                baseline = base.query("city != @city")
                if len(test) and len(baseline):
                    point_scores = scores(test, a, bootstrap=0)
                    base_scores = scores(baseline, a, bootstrap=0)
                    differences.append(point_scores["balanced_accuracy"] - base_scores["balanced_accuracy"])
                    kind_differences.append(point_scores["kind_precision"] - base_scores["kind_precision"])
            row["stable"] = len(differences) == 3 and all(np.isfinite(x) and x > 0 for x in differences)
            row["loco_differences"] = ";".join(f"{x:.6f}" for x in differences)
            row["stable_kind"] = len(kind_differences) == 3 and all(np.isfinite(x) and x > 0 for x in kind_differences)
            row["loco_kind_differences"] = ";".join(f"{x:.6f}" for x in kind_differences)
        chosen, reason = select_point(rows, current_id)
        kind_chosen, kind_reason = select_point(rows, current_id, "kind_precision", "stable_kind")
        recommendations[category_name] = {
            "decision": "keep current" if chosen["point_id"] == current_id else "override",
            "reason": reason,
            "point_id": chosen["point_id"],
            "overrides": {},
        }
        if chosen["point_id"] != current_id:
            p = override_category(category, chosen["values"])["policy"]
            recommendations[category_name]["overrides"] = {
                c: {k: p[c][k] for k in ("name_min", "near_m", "generic_m")}
                for c in ("yandex", "overture", "foursquare")
            }
            recommendations[category_name]["overrides"]["foursquare"]["fresh"] = p["foursquare"]["fresh"]
        recommendations[category_name]["kind_analysis"] = {
            "point_id": kind_chosen["point_id"],
            "reason": kind_reason,
            "values": dict(zip(DIMENSIONS, kind_chosen["values"], strict=True)),
        }
        pd.concat(predictions, ignore_index=True).to_csv(out / f"verdicts_{category_name}.csv", index=False)
        pd.DataFrame(rows).drop(columns="values").to_csv(out / f"metrics_{category_name}.csv", index=False)
        lines = [
            f"# Калибровочная сетка: {category_name}",
            "",
            f"Предварительно, размечено {len(category_labels)} из {len(part)}. "
            "Незавершённые метки не включены в оценки.",
            "Единицы зафиксированы по object_ids выборки A; свидетельства объединены внутри этих единиц.",
            "Исключены все параметры providers, включая name_min и relink, "
            "а также параметры общей записи и связывания.",
            "Расстояние до исходной конфигурации — число изменённых групп параметров; не более трёх.",
            "При оценке дисперсии одиночная нецензусная страта A объединяется в той же поставке: "
            "S2 с S1, иначе с S3; веса точечных оценок не меняются.",
            "ДИ: бутстреп по стратам с поправкой на конечную совокупность; также приведён интервал "
            "Вильсона с линеаризованной дисперсией объединённых страт. "
            "При неполной разметке ДИ и выбор не рассчитываются.",
            "При доле неясных меток более 20% основной результат — границы неясности; "
            "доли неясных меток приведены в metrics CSV.",
            "Страты с n_A = 0 не представлены в A и не входят в оценки; "
            "интервалы относятся только к представленным стратам.",
            f"Рекомендация по существованию: {reason}",
            f"Отдельный выбор по точности вида: {kind_chosen['point_id']}. {kind_reason}",
            "",
            "| Конфигурация | Яндекс имя | Overture/FSQ имя | FSQ с | Радиус | "
            "Precision [ДИ; bootstrap; неясные] | Recall [ДИ; bootstrap; неясные] | "
            "Accuracy [ДИ; bootstrap; неясные] | Вид precision [ДИ; bootstrap; неясные] | "
            "Покрытие вида | Balanced accuracy ± SE | LOCO |",
            "|---|---:|---:|---|---|---|---|---|---|---:|---|---|",
        ]

        def fmt(x):
            return f"{x:.3f}" if isinstance(x, (int, float, np.number)) and np.isfinite(x) else "—"

        def metric(row, name, bounds=False):
            val = fmt(row.get(name))
            ci = f"{fmt(row.get(name + '_low'))}–{fmt(row.get(name + '_high'))}"
            boot = f"{fmt(row.get(name + '_bootstrap_low'))}–{fmt(row.get(name + '_bootstrap_high'))}"
            unclear = (
                f"; {fmt(row.get(name + '_unclear_low'))}–{fmt(row.get(name + '_unclear_high'))}" if bounds else ""
            )
            return f"{val} [{ci}; {boot}{unclear}]"

        for row in rows:
            v = row["values"]
            cells = [
                row["point_id"],
                v[0],
                v[1],
                v[2],
                f"{v[3][0]}/{v[3][1]}",
                metric(row, "precision", True),
                metric(row, "recall", True),
                metric(row, "accuracy", True),
                metric(row, "kind_precision", True),
                fmt(row.get("kind_coverage")),
                f"{fmt(row.get('balanced_accuracy'))} ± {fmt(row.get('balanced_accuracy_se'))}",
                row["loco_differences"] or "—",
            ]
            lines.append("| " + " | ".join(map(str, cells)) + " |")
        (out / f"grid_{category_name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "recommended.yaml").write_text(
        yaml.safe_dump(recommendations, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    if args.bootstrap < 2:
        parser.error("--bootstrap must be at least 2")
    run(args.root, args.bootstrap)


if __name__ == "__main__":
    main()
