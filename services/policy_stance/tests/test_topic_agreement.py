"""topic_agreement_payload: who votes like a country at the UN, on a topic."""

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.main import topic_agreement_payload


def votes(*rows):
    return pd.DataFrame([{"country_name": c, "year": y, "resolution": r, "topic": t, "title": "", "vote_numeric": v} for c, y, r, t, v in rows])


HR = "HUMAN RIGHTS--REPORTS"
DATA = votes(
    # three human-rights resolutions
    ("Iran", 2020, "R1", HR, -1.0), ("Syria", 2020, "R1", HR, -1.0), ("Canada", 2020, "R1", HR, 1.0), ("India", 2020, "R1", HR, 0.0),
    ("Iran", 2021, "R2", HR, -1.0), ("Syria", 2021, "R2", HR, -1.0), ("Canada", 2021, "R2", HR, 1.0), ("India", 2021, "R2", HR, -1.0),
    ("Iran", 2022, "R3", HR, 1.0), ("Syria", 2022, "R3", HR, 1.0), ("Canada", 2022, "R3", HR, -1.0), ("India", 2022, "R3", HR, float("nan")),
    # an unrelated resolution where Canada agrees with Iran
    ("Iran", 2022, "R4", "LAW OF THE SEA", 1.0), ("Canada", 2022, "R4", "LAW OF THE SEA", 1.0), ("Syria", 2022, "R4", "LAW OF THE SEA", -1.0),
)


def test_ranks_partners_by_identical_votes_on_the_topic_only():
    out = topic_agreement_payload(DATA.dropna(subset=["vote_numeric"]), "iran", "human rights", min_shared=2)
    assert out["country"] == "Iran" and out["resolutions"] == 3
    assert [r["country"] for r in out["most_aligned"]] == ["Syria", "India", "Canada"]
    assert out["most_aligned"][0]["agreement"] == 1.0
    assert out["least_aligned"][0] == {"country": "Canada", "agreement": 0.0, "shared": 3, "opposed": 3}
    india = next(r for r in out["most_aligned"] if r["country"] == "India")
    assert india["shared"] == 2 and india["agreement"] == 0.5   # its absence on R3 is left out
    assert out["own_votes"] == {"yes": 1, "no": 2, "abstain": 0}


def test_no_topic_compares_every_resolution():
    out = topic_agreement_payload(DATA.dropna(subset=["vote_numeric"]), "Iran", "", min_shared=1)
    assert out["resolutions"] == 4
    canada = next(r for r in out["most_aligned"] if r["country"] == "Canada")
    assert canada["shared"] == 4 and canada["agreement"] == 0.25


def test_years_limit_the_resolutions():
    out = topic_agreement_payload(DATA.dropna(subset=["vote_numeric"]), "Iran", "human rights", start_year=2021, end_year=2021, min_shared=1)
    assert out["resolutions"] == 1


def test_a_named_pair_is_reported_whatever_its_rank():
    out = topic_agreement_payload(DATA.dropna(subset=["vote_numeric"]), "Iran", "human rights", limit=1, compare=("india",))
    assert [r["country"] for r in out["most_aligned"]] == ["Syria"]   # India is not in the top 1...
    assert out["pairs"] == [{"country": "India", "agreement": 0.5, "shared": 2, "opposed": 0}]


def test_unknown_country_is_a_404():
    with pytest.raises(HTTPException) as err:
        topic_agreement_payload(DATA, "Atlantis")
    assert err.value.status_code == 404


def test_a_state_that_voted_only_briefly_is_not_ranked():
    rows = [("Iran", 2000 + i, f"R{i}", HR, -1.0) for i in range(12)] + [("Canada", 2000 + i, f"R{i}", HR, 1.0) for i in range(12)]
    rows += [("East Germany", 2000, "R0", HR, -1.0), ("East Germany", 2001, "R1", HR, -1.0)]   # 2 of 12: under a quarter
    out = topic_agreement_payload(votes(*rows), "Iran", "human rights", min_shared=1)
    assert [r["country"] for r in out["most_aligned"]] == ["Canada"]
