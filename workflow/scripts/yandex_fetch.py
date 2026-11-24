"""Rule `yandex_fetch`: read the missing Yandex pages into the cache (network).

One delivery after another, so a cache has one writer; a failure is not
cached and is asked again next run; every delivery is read before the rule
fails over one.  The CLI is `infracheck.checks.yandex`.
"""

import json
import subprocess
import sys

failed = []
with open(snakemake.log[0], "w", encoding="utf-8") as log:
    for objects, queue, cache, policy in zip(snakemake.input.objects, snakemake.input.queues,
                                             snakemake.params.caches, snakemake.params.policies, strict=True):
        cmd = [sys.executable, "-m", "infracheck.checks.yandex", "fetch", "--objects", objects, "--cache", cache,
               "--only-ids", queue, "--sleep", *map(str, snakemake.params.sleep),
               "--policy-json", json.dumps(dict(policy or {}), ensure_ascii=False)]
        if snakemake.params.limit:
            cmd += ["--limit", str(snakemake.params.limit)]
        print(" ".join(cmd), file=log, flush=True)
        if subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode:
            failed.append(objects)
if failed:
    sys.exit(f"Yandex fetch had failed requests in: {', '.join(failed)}")
