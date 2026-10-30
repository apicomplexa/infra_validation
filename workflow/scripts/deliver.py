"""Rule `deliver`: the confirmed copy of the delivered workbook."""

import logging
from pathlib import Path

from infracheck.categories import base, pipeline, registry
from infracheck.deliver import xlsx

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

d = snakemake.params.delivery
cat = snakemake.params.category
spec = base.Spec.from_config(d["category"], cat)
dst = Path(snakemake.output[0])
if dst.name != xlsx.output_name(d["path"], cat["deliver"]):
    raise ValueError(f"{dst.name}: not the deliverable name of {d['path']}")
_, rowmap, raw = pipeline.load(d)
table = pipeline.read_parquet(snakemake.input.objects)
summary = pipeline.deliver(snakemake.input.xlsx, dst, raw, rowmap, table, spec, registry.policies(cat))
logging.info("wrote %s (%d rows)", dst, len(raw))
