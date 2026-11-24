"""Rule `yandex_queue`: the objects our own Yandex query could still decide, one id per line."""

import logging
from pathlib import Path

import pandas as pd

from infracheck.categories import base, pipeline, registry

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

cat = snakemake.params.category
spec = base.Spec.from_config("", cat)
ob = pipeline.read_parquet(snakemake.input.objects)
evs = {c: pipeline.read_parquet(getattr(snakemake.input, c)) for c in spec.checks if c != "yandex"}
# The contractor's re-judged hit costs no request: with an empty cache the
# yandex check yields exactly those rows.
evs["yandex"] = registry.run("yandex", ob, registry.policy("yandex", cat["policy"].get("yandex")), None)
need = base.needs_query(registry.stack(evs), ob.index, spec)
if spec.query_all:  # closure check: ask for everything, the verdict rule is unchanged
    logging.info("query.all: %d decisive of %d objects", int(need.sum()), len(ob))
    # an unnamed object (the contractor wrote its type as the name) has nothing to look up
    types = ob["types"] if "types" in ob else pd.Series("", index=ob.index)
    unnamed = [str(n).strip() in str(t).split("; ") for n, t in zip(ob["name"], types, strict=True)]
    need = need | ~pd.Series(unnamed, index=ob.index)  # every named object, and the decisive unnamed
Path(snakemake.output[0]).write_text("".join(f"{i}\n" for i in ob.index[need]), encoding="utf-8")
logging.info("%d of %d objects need our Yandex query", int(need.sum()), len(ob))
