# Our own Yandex query -- the one network step, kept out of `all`.
#
#   pixi run yandex-fetch      # = snakemake yandex_fetch --forcerun yandex_fetch
#
# `yandex_queue` lists, per delivery, the objects whose verdict the query could
# still change (the category's `query` block), computed without our own Yandex
# rows, so it does not depend on the cache it is about to fill. `yandex_fetch`
# reads the missing pages into the cache, one delivery after another (one
# writer per cache); the next `pixi run workflow` sees the newer cache and
# re-judges.


rule yandex_queue:
    input:
        unpack(lambda wildcards: {c: p for c, p in evidence_files(wildcards).items() if c != "yandex"}),
        objects="<temp>/{delivery}/objects.parquet",
    output:
        "<temp>/{delivery}/yandex_queue.txt",
    log:
        "<logs>/yandex_queue/{delivery}.log",
    conda:
        "../envs/default.yaml"
    params:
        category=category_cfg,
    script:
        "../scripts/yandex_queue.py"


rule yandex_fetch:
    input:
        queues=expand("<temp>/{delivery}/yandex_queue.txt", delivery=DELIVERIES.index),
        objects=expand("<temp>/{delivery}/objects.parquet", delivery=DELIVERIES.index),
    output:
        touch("<logs>/yandex_fetch.done"),
    log:
        "<logs>/yandex_fetch.log",
    conda:
        "../envs/default.yaml"
    params:
        caches=[config["resources"]["yandex_cache"][category_of(d)["yandex_cache"]] for d in DELIVERIES.index],
        policies=[category_of(d)["policy"].get("yandex", {}) for d in DELIVERIES.index],
        limit=config["yandex_fetch"]["limit"],
        sleep=config["yandex_fetch"]["sleep"],
    script:
        "../scripts/yandex_fetch.py"
