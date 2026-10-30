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


rule summary_category:
    # One category's summary blocks (in delivery-sheet order) and all its
    # decided objects in one CSV; for catering these are the archive's
    # final_confirm_summary.txt / final_confirm_objects.csv.
    input:
        summaries=lambda wildcards: [f"<results>/{d}/summary.txt" for d in deliveries_of(wildcards.category)],
        objects=lambda wildcards: [f"<results>/{d}/objects.parquet" for d in deliveries_of(wildcards.category)],
    output:
        summary="<results>/{category}_summary.txt",
        objects="<results>/{category}_objects.csv",
    log:
        "<logs>/summary_category/{category}.log",
    conda:
        "../envs/default.yaml"
    params:
        files=lambda wildcards: [Path(DELIVERIES.loc[d, "path"]).name for d in deliveries_of(wildcards.category)],
    script:
        "../scripts/summary_category.py"
