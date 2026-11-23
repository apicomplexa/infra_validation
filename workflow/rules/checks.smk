# One rule per check: ObjectTable (+ the check's own resource) -> Evidence.
# Each rule carries only its check's Policy block as a param, so moving one
# threshold re-runs that check and what depends on it, nothing else.


rule check_providers:
    input:
        objects="<temp>/{delivery}/objects.parquet",
    output:
        "<temp>/{delivery}/evidence/providers.parquet",
    log:
        "<logs>/check_providers/{delivery}.log",
    conda:
        "../envs/default.yaml"
    params:
        check="providers",
        policy=policy_cfg("providers"),
    script:
        "../scripts/check.py"


use rule check_providers as check_arkh_admin with:
    output:
        "<temp>/{delivery}/evidence/arkh_admin.parquet",
    log:
        "<logs>/check_arkh_admin/{delivery}.log",
    params:
        check="arkh_admin",
        policy=policy_cfg("arkh_admin"),


use rule check_providers as check_register_flag with:
    output:
        "<temp>/{delivery}/evidence/register_flag.parquet",
    log:
        "<logs>/check_register_flag/{delivery}.log",
    params:
        check="register_flag",
        policy=policy_cfg("register_flag"),


use rule check_providers as check_export_name with:
    output:
        "<temp>/{delivery}/evidence/export_name.parquet",
    log:
        "<logs>/check_export_name/{delivery}.log",
    params:
        check="export_name",
        policy=policy_cfg("export_name"),


use rule check_providers as check_yandex with:
    # Judges the cached answers only; the cache is filled by `yandex_fetch`.
    input:
        objects="<temp>/{delivery}/objects.parquet",
        resource=yandex_cache,
    output:
        "<temp>/{delivery}/evidence/yandex.parquet",
    log:
        "<logs>/check_yandex/{delivery}.log",
    params:
        check="yandex",
        policy=policy_cfg("yandex"),


use rule check_providers as check_overture with:
    input:
        objects="<temp>/{delivery}/objects.parquet",
        resource=config["resources"]["overture"],
    output:
        "<temp>/{delivery}/evidence/overture.parquet",
    log:
        "<logs>/check_overture/{delivery}.log",
    params:
        check="overture",
        policy=policy_cfg("overture"),


use rule check_providers as check_foursquare with:
    input:
        objects="<temp>/{delivery}/objects.parquet",
        resource=config["resources"]["foursquare"],
    output:
        "<temp>/{delivery}/evidence/foursquare.parquet",
    log:
        "<logs>/check_foursquare/{delivery}.log",
    params:
        check="foursquare",
        policy=policy_cfg("foursquare"),


rule egais_group:
    # The registry join over every delivery of a match group at once: the v4
    # rule gives one outlet to one shop across the alcohol and tobacco layers.
    # A catering delivery is a group of its own.
    input:
        objects=group_objects,
        resource="<temp>/egais/registry.tsv",
    output:
        "<temp>/egais/groups/{group}.parquet",
    log:
        "<logs>/egais_group/{group}.log",
    conda:
        "../envs/default.yaml"
    params:
        policy=group_policy,
    script:
        "../scripts/egais_group.py"


rule check_egais:
    # One delivery's rows of its group's join.
    input:
        objects="<temp>/{delivery}/objects.parquet",
        group=group_evidence,
    output:
        "<temp>/{delivery}/evidence/egais.parquet",
    log:
        "<logs>/check_egais/{delivery}.log",
    conda:
        "../envs/default.yaml"
    script:
        "../scripts/egais_split.py"
