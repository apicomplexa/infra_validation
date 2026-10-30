"""Check rules: ObjectTable (+ the check's resource) -> Evidence of one check."""

import logging

from infracheck.categories import pipeline, registry

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

check = snakemake.params.check
ob = pipeline.read_parquet(snakemake.input.objects)
policy = registry.policy(check, snakemake.params.policy)
ev = registry.run(check, ob, policy, getattr(snakemake.input, "resource", None))
pipeline.write_parquet(ev, snakemake.output[0])
logging.info("%s: %d objects -> %d evidence rows (%s)", check, len(ob), len(ev),
             ev.groupby(["identity", "record_state"]).size().to_dict())
