"""Rule `summary_category`: one category's summary blocks, and all its decided objects in one CSV."""

import logging
from pathlib import Path

import pandas as pd

from infracheck.categories import base, pipeline

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

blocks = [Path(p).read_text(encoding="utf-8") for p in snakemake.input.summaries]
Path(snakemake.output.summary).write_text(base.combined_summary(blocks), encoding="utf-8")
tables = [pipeline.read_parquet(p).assign(file=f) for p, f in zip(snakemake.input.objects, snakemake.params.files,
                                                                  strict=True)]
pd.concat(tables).to_csv(snakemake.output.objects, sep=";", encoding="utf-8-sig", index=True)
logging.info("%d deliveries", len(blocks))
