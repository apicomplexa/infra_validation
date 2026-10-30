"""Download a snapshot of Foursquare OS Places around the three final-delivery cities.

FSQ OS Places (Apache 2.0) is the full Foursquare open layer; Overture carries
only a few hundred of its rows here.  Since 2025-10 it is published as a
**gated** Hugging Face dataset (`foursquare/fsq-os-places`), so the run needs
a Hugging Face account that has accepted the dataset's terms and a read token
saved where `huggingface_hub` keeps it (`hf auth login` writes
`~/.cache/huggingface/token`); DuckDB picks it up from there or from
`HF_TOKEN`.  The token is never written into this repository.

    python -m infracheck.fetch.foursquare --out-dir resources/foursquare
    python -m infracheck.fetch.foursquare --release 2026-10-15

Only the bounding box is read: DuckDB pushes the latitude/longitude filter
into the parquet row-group statistics, so ~12 GB of release is not downloaded
whole.  Written to `resources/foursquare/`, read-only afterwards.
`date_closed` is kept -- a place Foursquare marks closed is not evidence of
existence.
"""
from __future__ import annotations

import argparse
from pathlib import Path

RELEASE = "2026-10-15"
# Same box as fetch.overture: Архангельск, Северодвинск, Новодвинск.
BBOX = {"lon": (39.70, 40.90), "lat": (64.38, 64.73)}
OUT_DIR = Path(__file__).resolve().parents[3] / "resources" / "foursquare"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--release", default=RELEASE)
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = p.parse_args(argv)

    import duckdb
    out = args.out_dir / f"fsq_places_{args.release}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.sql("INSTALL httpfs; LOAD httpfs;")
    c.sql("CREATE SECRET hf (TYPE huggingface, PROVIDER credential_chain)")
    (lo0, lo1), (la0, la1) = BBOX["lon"], BBOX["lat"]
    src = f"hf://datasets/foursquare/fsq-os-places/release/dt={args.release}/places/parquet/*.parquet"
    c.sql(f"""
        COPY (
          SELECT fsq_place_id, name, latitude AS lat, longitude AS lon,
                 address, locality, region, postcode,
                 date_created, date_refreshed, date_closed,
                 tel, website, instagram,
                 array_to_string(fsq_category_labels, '|') AS categories
          FROM read_parquet('{src}')
          WHERE longitude BETWEEN {lo0} AND {lo1} AND latitude BETWEEN {la0} AND {la1}
        ) TO '{out}' (FORMAT parquet)""")
    n, closed = c.sql(f"SELECT count(*), count(date_closed) FROM '{out}'").fetchall()[0]
    print(f"wrote {out}  ({n:,} places, {closed:,} marked closed)")
    print(c.sql(f"SELECT locality, count(*) n FROM '{out}' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
