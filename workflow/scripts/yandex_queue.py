"""Rule `yandex_queue`: the objects our own Yandex query could still decide, one id per line."""

import logging
from pathlib import Path

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
Path(snakemake.output[0]).write_text("".join(f"{i}\n" for i in ob.index[need]), encoding="utf-8")
logging.info("%d of %d objects need our Yandex query", int(need.sum()), len(ob))
