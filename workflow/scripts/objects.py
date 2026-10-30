"""Rule `objects`: a delivery file -> ObjectTable + RowMap (the input adapter by `format`)."""

import logging

from infracheck.categories import pipeline

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

d = snakemake.params.delivery
ob, rowmap, raw = pipeline.load(d)
pipeline.write_parquet(ob, snakemake.output.objects)
pipeline.write_parquet(rowmap, snakemake.output.rowmap)
logging.info("%s: %d rows -> %d objects", d["path"], len(raw), len(ob))
