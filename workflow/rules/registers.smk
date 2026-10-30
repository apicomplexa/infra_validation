# Registers: raw snapshots -> the extract the checks read.


rule egais_register:
    # Streams the 10.9 GB registry out of its ZIP (~6 min) and keeps the rows
    # of every city in the delivery sheet.
    input:
        config["resources"]["egais_zip"],
    output:
        "<temp>/egais/registry.tsv",
    log:
        "<logs>/egais_register.log",
    conda:
        "../envs/default.yaml"
    params:
        settlements=settlements(),
    shell:
        "python -m infracheck.registers.egais parse --zip {input:q} --settlements {params.settlements} --out {output:q} >{log:q} 2>&1"
