# Input adapters: a delivery file -> ObjectTable (one row per object) + RowMap.


rule objects:
    input:
        lambda wildcards: DELIVERIES.loc[wildcards.delivery, "path"],
    output:
        objects="<temp>/{delivery}/objects.parquet",
        rowmap="<temp>/{delivery}/rowmap.parquet",
    log:
        "<logs>/objects/{delivery}.log",
    conda:
        "../envs/default.yaml"
    params:
        delivery=delivery_row,
    script:
        "../scripts/objects.py"
