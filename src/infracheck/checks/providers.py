"""OSM and 2GIS: the provider flags of the export, and the missed merges.

The final delivery ends each row with source flags (`OSM`, `GIS`).  They are
read as they stand (`check = "providers"`); on top of that, **the contractor's
merge missed pairs** -- «1234» (OSM only) and «1234, кофейня» (OSM + 2GIS) stand
14 m apart as two objects.  Where an object lacks a provider and a near,
alike-named object of the *other* provider stands within `Policy.relink_m`,
that provider is lent to it (`relink`, `check = "relink"`) and the row says
which record lent it.

OSM, 2GIS and Yandex are three companies' directories.  Flamp (2GIS) and
Яндекс Еда (Yandex) would not be new sources, and must not be added as such.

Ported from `arkhinfra.final_confirm` (`relink`, `_osm_gis_reason`).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from infracheck.core import evidence
from infracheck.core.geo import dist_matrix
from infracheck.core.names import NAME_MIN, similar

PROVIDERS = {"osm": ("OSM", "osm"), "gis": ("2GIS", "2gis")}   # flag -> (label, source)


@dataclass(frozen=True)
class Policy:
    """A near pair from the two providers is the contractor's own missed merge
    only at a building's depth; a nameless fascia ("Столовая") has to be closer
    still, since every canteen matches every other by name."""

    relink_m: float = 50.0
    relink_generic_m: float = 20.0
    name_min: float = NAME_MIN


DEFAULT = Policy()


def relink(ob: pd.DataFrame, policy: Policy = DEFAULT) -> pd.DataFrame:
    """Lend a provider to an object from the other provider's twin next door.

    Symmetric name test (the weaker direction of `name_similarity`), because
    both sides are records of the same export and neither is the "directory".
    Within one city only; nearest qualifying twin wins.  Returns `ob` with
    `osm_link`, `gis_link`, `link_note`, `osm_link_from`, `gis_link_from`
    (and the metres, `osm_link_m` / `gis_link_m`, the twin's name,
    `osm_link_name` / `gis_link_name`, and its index label, `osm_link_obj` /
    `gis_link_obj`: the twin is the same sign recorded twice, which the
    category's object count merges).
    """
    ob = ob.assign(osm_link=False, gis_link=False, link_note="",
                   osm_link_from="", gis_link_from="",
                   osm_link_m=np.nan, gis_link_m=np.nan, osm_link_name="", gis_link_name="",
                   osm_link_obj="", gis_link_obj="")
    for _, part in ob.groupby("city"):
        idx = part.index.to_numpy()
        d = dist_matrix(part["lat"].to_numpy(float), part["lon"].to_numpy(float))
        np.fill_diagonal(d, np.inf)
        for a_pos, a in enumerate(idx):
            for prov, (label, _) in PROVIDERS.items():
                if ob.at[a, prov]:
                    continue
                cand = np.where((d[a_pos] <= policy.relink_m) & part[prov].to_numpy(bool))[0]
                for b_pos in cand[np.argsort(d[a_pos, cand])]:
                    b = idx[b_pos]
                    limit = (policy.relink_generic_m if (ob.at[a, "generic"] or ob.at[b, "generic"])
                             else policy.relink_m)
                    if d[a_pos, b_pos] > limit:
                        continue
                    na, nb = ob.at[a, "name"], ob.at[b, "name"]
                    sim = min(similar(na, nb), similar(nb, na))
                    if sim < policy.name_min:
                        continue
                    ob.at[a, f"{prov}_link"] = True
                    note = f"{label} из записи «{nb}» ({d[a_pos, b_pos]:.0f} м)"
                    urls = ob.at[b, f"{prov}_urls"]
                    ob.at[a, f"{prov}_link_from"] = f"«{nb}», {d[a_pos, b_pos]:.0f} м" + (
                        f", {urls}" if urls else "")
                    ob.at[a, f"{prov}_link_m"] = float(d[a_pos, b_pos])
                    ob.at[a, f"{prov}_link_name"] = nb
                    ob.at[a, f"{prov}_link_obj"] = str(b)
                    ob.at[a, "link_note"] = "; ".join(filter(None, [ob.at[a, "link_note"], note]))
                    break
    return ob


def run(objects: pd.DataFrame, policy: Policy = DEFAULT) -> pd.DataFrame:
    """Evidence of OSM and 2GIS for every object.

    * `providers`: the export's own flag -- identity strong, record current,
      text «запись выгрузки (urls)»; `ref` is the first record URL, the
      extra column `providers_urls` all of them.
    * `relink`: the flag lent by a twin -- same axes, text «соседняя запись …»;
      `matched_name` is the twin's name, `distance_m` the metres to it; extra
      columns `relink_note` (the piece of «Связанная запись»), `relink_from`
      and `relink_object` (the twin's object_id).

    No row where the object has neither.
    """
    rows = []
    linked = relink(objects, policy)
    for oid, r in linked.iterrows():
        for prov, (label, source) in PROVIDERS.items():
            if r[prov]:
                urls = r[f"{prov}_urls"]
                rows.append({
                    "object_id": oid, "check": "providers", "source": source,
                    "identity": "strong", "record_state": "current",
                    "ref": urls.split(" ")[0] if urls else "", "label": label,
                    "text": "запись выгрузки" + (f" ({urls})" if urls else ""),
                    "providers_urls": urls})
            elif r[f"{prov}_link"]:
                rows.append({
                    "object_id": oid, "check": "relink", "source": source,
                    "identity": "strong", "record_state": "current",
                    "matched_name": r[f"{prov}_link_name"], "distance_m": r[f"{prov}_link_m"],
                    "label": label, "text": f"соседняя запись {r[f'{prov}_link_from']}",
                    "relink_note": f"{label} из записи «{r[f'{prov}_link_name']}»"
                                   f" ({r[f'{prov}_link_m']:.0f} м)",
                    "relink_from": r[f"{prov}_link_from"], "relink_object": r[f"{prov}_link_obj"]})
    if not rows:
        return evidence.empty()
    return evidence.make(rows)


def wide(ev: pd.DataFrame, index: pd.Index | None = None) -> pd.DataFrame:
    """The archive's columns, indexed by object_id: `osm`, `gis`, `osm_link`,
    `gis_link`, `link_note`, `osm_link_from`, `gis_link_from`, `osm_urls`,
    `gis_urls`.  Objects without evidence get False / "" (pass `index` to
    keep all objects)."""
    ids = index if index is not None else pd.Index(ev["object_id"].unique(), name="object_id")
    out = pd.DataFrame(index=ids)
    out.index.name = "object_id"
    prov = ev[ev["check"] == "providers"]
    link = ev[ev["check"] == "relink"]
    for flag, (_, source) in PROVIDERS.items():
        p = prov[prov["source"] == source].set_index("object_id")
        k = link[link["source"] == source].set_index("object_id")
        out[flag] = out.index.isin(p.index)
        out[f"{flag}_link"] = out.index.isin(k.index)
        out[f"{flag}_link_from"] = k["relink_from"].reindex(out.index).fillna("").astype(object)
        out[f"{flag}_urls"] = p["providers_urls"].reindex(out.index).fillna("").astype(object)
    # «Связанная запись»: OSM's note first, then 2GIS's -- the order `relink` wrote them.
    notes = []
    for oid in out.index:
        parts = [link[(link["object_id"] == oid) & (link["source"] == s)]["relink_note"]
                 for s in ("osm", "2gis")]
        notes.append("; ".join(p.iloc[0] for p in parts if len(p)))
    out["link_note"] = notes
    return out[["osm", "gis", "osm_link", "gis_link", "link_note", "osm_link_from",
                "gis_link_from", "osm_urls", "gis_urls"]]
