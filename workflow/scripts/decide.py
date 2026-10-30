"""Rule `decide`: every check's Evidence -> the decided object table + its summary block."""

import logging
from pathlib import Path

from infracheck.categories import base, pipeline

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

d = snakemake.params.delivery
spec = base.Spec.from_config(d["category"], snakemake.params.category)
ob = pipeline.read_parquet(snakemake.input.objects)
rowmap = pipeline.read_parquet(snakemake.input.rowmap)
evs = {c: pipeline.read_parquet(getattr(snakemake.input, c)) for c in spec.checks}
table = pipeline.decide(ob, evs, spec)
pipeline.write_parquet(table, snakemake.output.objects)
Path(snakemake.output.summary).write_text(pipeline.summary_block(Path(d["path"]).name, table, rowmap, spec),
                                          encoding="utf-8")
logging.info("%s: %d of %d objects confirmed", d["delivery"], int(table["confirmed"].sum()), len(table))
