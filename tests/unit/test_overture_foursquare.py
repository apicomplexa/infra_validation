"""Candidate search, exclusivity and freshness states of the Overture / Foursquare checks."""
from __future__ import annotations

import pandas as pd

from infracheck.checks import _directory as D
from infracheck.checks import foursquare, overture


def _objects(rows):
    df = pd.DataFrame(rows, columns=["object_id", "name", "address", "lat", "lon", "generic"])
    df["city"] = "X"
    return df.set_index("object_id")


def _fsq(**kw):
    base = dict(fsq_place_id="f1", name="Йоко", lat=64.5, lon=40.5, address="", date_refreshed="2024-05-01",
                date_closed="", categories="Dining and Drinking > Restaurant")
    base.update(kw)
    return pd.DataFrame([base])


def test_candidates_radius_and_order():
    pl = pd.DataFrame({"lat": [64.5, 64.5005, 64.502], "lon": [40.5] * 3})
    pos, d = D.candidates(64.5, 40.5, False, pl)
    assert list(pos) == [0, 1] and d[2] > 150
    pos, _ = D.candidates(64.5, 40.5, True, pl)
    assert list(pos) == [0, 1]
    pos, _ = D.candidates(64.5, 40.5, True, pl, allowed=pd.Series([False, True, True]).to_numpy())
    assert list(pos) == [1]


def test_exclusive_nearest_wins_and_duplicates_within_relink_keep():
    ob = _objects([("a", "Хинди", "Троицкий 61", 64.5, 40.5, False),
                   ("b", "Хинди", "Троицкий 67", 64.5006, 40.5, False),
                   ("c", "Хинди, кафе", "Троицкий 61", 64.50001, 40.5, False)])
    ev = pd.DataFrame({"object_id": ["a", "b", "c"], "ref": ["r"] * 3, "distance_m": [5.0, 40.0, 6.0],
                       "identity": "strong", "note": ""})
    out = D.exclusive(ev, ob, note_col="note")
    assert list(out["identity"]) == ["strong", "weak", "strong"]
    assert out.at[1, "note"].startswith("запись ближе к объекту «Хинди» (Троицкий 61), 67 м")


def test_overture_run_and_wide():
    ob = _objects([("a", "Йоко", "x", 64.5, 40.5, False), ("b", "Другое", "y", 64.6, 40.6, False)])
    pl = pd.DataFrame([dict(id="o1", name="Йоко", category="restaurant", confidence=0.9, dataset="meta",
                            datasets="meta|foursquare", address="", lat=64.5001, lon=40.5),
                       dict(id="o2", name="Йоко", category="restaurant", confidence=0.3, dataset="meta",
                            datasets="meta", address="", lat=64.5, lon=40.5)])
    ev = overture.run(ob, pl)
    assert len(ev) == 1 and ev.at[0, "origin"] == "meta|foursquare"
    assert ev.at[0, "text"].startswith("«Йоко» (restaurant), ")
    w = overture.wide(ev, ob.index)
    assert list(w["ov_match"]) == [True, False] and w.at["b", "ov_name"] == ""


def test_foursquare_freshness_states():
    ob = _objects([("a", "Йоко", "x", 64.5, 40.5, False)])
    cases = [({}, "strong", "current", ""),
             ({"date_refreshed": "2014-03-02"}, "strong", "stale", "запись не обновлялась с 2014"),
             ({"date_closed": "2023-01-01"}, "strong", "closed", "закрыт с 2023-01-01")]
    for kw, ident, state, note in cases:
        ev = foursquare.run(ob, _fsq(**kw))
        assert (ev.at[0, "identity"], ev.at[0, "record_state"], ev.at[0, "foursquare_note"]) == (ident, state, note)
        assert ev.at[0, "kind_text"] == "Dining and Drinking > Restaurant"
    w = foursquare.wide(foursquare.run(ob, _fsq(date_refreshed="2014-03-02")), ob.index)
    assert not w.at["a", "fsq_match"] and w.at["a", "fsq_category"]


def test_foursquare_fresh_beats_nearer_stale():
    ob = _objects([("a", "Йоко", "x", 64.5, 40.5, False)])
    pl = pd.concat([_fsq(fsq_place_id="old", date_refreshed="2014-01-01"),
                    _fsq(fsq_place_id="new", lat=64.5005)], ignore_index=True)
    assert foursquare.run(ob, pl).at[0, "ref"] == "new"
