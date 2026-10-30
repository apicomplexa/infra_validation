"""Rule `yandex_fetch`: read the missing Yandex pages into the cache (network).

One delivery after another, so a cache has one writer; a failure is not
cached and is asked again next run.  The CLI is `infracheck.checks.yandex`.
"""

import subprocess
import sys

with open(snakemake.log[0], "w", encoding="utf-8") as log:
    for objects, queue, cache in zip(snakemake.input.objects, snakemake.input.queues, snakemake.params.caches,
                                     strict=True):
        cmd = [sys.executable, "-m", "infracheck.checks.yandex", "fetch", "--objects", objects, "--cache", cache,
               "--only-ids", queue, "--sleep", *map(str, snakemake.params.sleep)]
        if snakemake.params.limit:
            cmd += ["--limit", str(snakemake.params.limit)]
        print(" ".join(cmd), file=log, flush=True)
        subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT)
