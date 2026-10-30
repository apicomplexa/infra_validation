"""Rule `egais_group`: the licence registry join over every delivery of one match group."""

import logging

from infracheck.categories import pipeline, registry

logging.basicConfig(filename=snakemake.log[0], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

obs = [pipeline.read_parquet(p) for p in snakemake.input.objects]
policy = registry.policy("egais", snakemake.params.policy)
ev = pipeline.egais_group(obs, policy, snakemake.input.resource)
pipeline.write_parquet(ev, snakemake.output[0])
logging.info("%s: %d deliveries, %d objects -> %d rows (%s)", snakemake.wildcards.group, len(obs),
             sum(len(o) for o in obs), len(ev), ev.groupby(["identity", "record_state"]).size().to_dict())
