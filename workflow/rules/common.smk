# Shared code: config / delivery-sheet validation and input helper functions.
# Imported first by the Snakefile, so everything defined here is visible to all rules.
from pathlib import Path

import pandas as pd
from snakemake.utils import validate

from infracheck.deliver import xlsx as deliver_xlsx

# Fails early with a readable message on a missing key or a typo in config/config.yaml.
validate(config, schema="../schemas/config.schema.yaml")

# Row order is kept: it is the order of the combined summary.
DELIVERIES = pd.read_csv(config["deliveries"], sep="\t", dtype=str, comment="#").set_index("delivery", drop=False)
validate(DELIVERIES, schema="../schemas/deliveries.schema.yaml")

for _cat in DELIVERIES["category"].unique():
    if _cat not in config["categories"]:
        raise ValueError(f"config/deliveries.tsv names category {_cat!r}, which config.yaml does not define")


# Match groups: deliveries matched against the licence registry together, in
# row order (the v4 join: one outlet per shop across both risk layers).
GROUPS = {g: list(d["delivery"]) for g, d in DELIVERIES.groupby("match_group", sort=False)}
GROUP_OF = DELIVERIES["match_group"].to_dict()
for _g, _members in GROUPS.items():
    _pols = [config["categories"][DELIVERIES.loc[m, "category"]]["policy"].get("egais") for m in _members]
    if any(p != _pols[0] for p in _pols):
        raise ValueError(f"match_group {_g!r}: its deliveries' categories carry different egais policies")


wildcard_constraints:
    delivery="|".join(DELIVERIES.index),
    group="|".join(GROUPS),
    category="|".join(DELIVERIES["category"].unique()),
    name=r"[^/]+\.xlsx",


def delivery_row(wildcards):
    """The delivery's row of deliveries.tsv as a plain dict (a rule param)."""
    return DELIVERIES.loc[wildcards.delivery].to_dict()


def category_of(delivery):
    return config["categories"][DELIVERIES.loc[delivery, "category"]]


def category_cfg(wildcards):
    """The whole category block: a param of decide/deliver, so any change re-runs them."""
    return category_of(wildcards.delivery)


def policy_cfg(check):
    """Param function: the check's policy block for the delivery's category."""
    return lambda wildcards: category_of(wildcards.delivery).get("policy", {}).get(check, {})


def evidence_files(wildcards):
    """{check: evidence parquet} for the checks of the delivery's category."""
    return {c: f"<temp>/{wildcards.delivery}/evidence/{c}.parquet" for c in category_of(wildcards.delivery)["checks"]}


def yandex_cache(wildcards):
    return config["resources"]["yandex_cache"][category_of(wildcards.delivery)["yandex_cache"]]


def output_name(delivery):
    """`<original stem>_подтверждено.xlsx`: the archive's name for the copy."""
    d = DELIVERIES.loc[delivery]
    return deliver_xlsx.output_name(d["path"], category_of(delivery)["deliver"])


def deliverables():
    return [f"<results>/{d}/{output_name(d)}" for d in DELIVERIES.index]


def settlements():
    """The registry settlements to keep: every city of the delivery sheet, lower case."""
    return sorted({c.strip().lower() for c in DELIVERIES["city"]})


def group_objects(wildcards):
    """The ObjectTables of a match group's deliveries, in delivery-sheet order."""
    return [f"<temp>/{d}/objects.parquet" for d in GROUPS[wildcards.group]]


def group_policy(wildcards):
    """The egais policy every delivery of the group shares (checked above)."""
    return category_of(GROUPS[wildcards.group][0])["policy"].get("egais", {})


def group_evidence(wildcards):
    return f"<temp>/egais/groups/{GROUP_OF[wildcards.delivery]}.parquet"


def deliveries_of(category):
    return [d for d in DELIVERIES.index if DELIVERIES.loc[d, "category"] == category]


def category_summaries():
    cats = list(dict.fromkeys(DELIVERIES["category"]))
    return [f"<results>/{c}_summary.txt" for c in cats] + [f"<results>/{c}_objects.csv" for c in cats]
