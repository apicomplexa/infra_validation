"""Rule `check_egais`: one delivery's rows of its match group's registry join."""

import logging

from infracheck.categories import pipeline

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ob = pipeline.read_parquet(snakemake.input.objects)
ev = pipeline.split_group(pipeline.read_parquet(snakemake.input.group), ob.index)
if len(ev) != len(ob):
    raise RuntimeError(f"{snakemake.wildcards.delivery}: {len(ev)} registry rows for {len(ob)} objects")
pipeline.write_parquet(ev, snakemake.output[0])
logging.info("%d rows", len(ev))
