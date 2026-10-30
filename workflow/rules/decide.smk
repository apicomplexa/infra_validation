# The category rule and the deliverables.


rule decide:
    input:
        unpack(evidence_files),
        objects="<temp>/{delivery}/objects.parquet",
        rowmap="<temp>/{delivery}/rowmap.parquet",
    output:
        objects="<results>/{delivery}/objects.parquet",
        summary="<results>/{delivery}/summary.txt",
    log:
        "<logs>/decide/{delivery}.log",
    conda:
        "../envs/default.yaml"
    params:
        delivery=delivery_row,
        category=category_cfg,
    script:
        "../scripts/decide.py"


rule deliver:
    # A copy of the delivered workbook: verdict in front, evidence appended,
    # the legend sheet. The raw file is only read.
    input:
        xlsx=lambda wildcards: DELIVERIES.loc[wildcards.delivery, "path"],
        objects="<results>/{delivery}/objects.parquet",
    output:
        "<results>/{delivery}/{name}",
    log:
        "<logs>/deliver/{delivery}/{name}.log",
    conda:
        "../envs/default.yaml"
    params:
        delivery=delivery_row,
        category=category_cfg,
    script:
        "../scripts/deliver.py"


rule summary_all:
    input:
        summaries=expand("<results>/{delivery}/summary.txt", delivery=DELIVERIES.index),
        objects=expand("<results>/{delivery}/objects.parquet", delivery=DELIVERIES.index),
    output:
        summary="<results>/final_confirm_summary.txt",
        objects="<results>/final_confirm_objects.csv",
    log:
        "<logs>/summary_all.log",
    conda:
        "../envs/default.yaml"
    params:
        files=[Path(p).name for p in DELIVERIES["path"]],
    script:
        "../scripts/summary_all.py"
