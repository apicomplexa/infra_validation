# workflow/envs/ — generated, do not edit

`default.yaml` is exported from `pixi.toml` + `pixi.lock` by `pixi run
export-envs` (run automatically before `pixi run workflow`, `dry-run` and
`lint`, and by the pre-commit hook). Every rule names it
(`conda: "../envs/default.yaml"`), so each step documents the software it runs
with and `snakemake --lint` is satisfied.

It is **not deployed**: the profile sets no software-deployment method, and
every rule runs in the pixi `default` env that runs Snakemake itself. The file
lists `infracheck` and `licmatch` as editable pip installs relative to the
repository root, so `--sdm conda` would not work with it as it stands.

To change the environment: edit `pixi.toml`, then `pixi run export-envs`.
