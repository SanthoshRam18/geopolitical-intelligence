"""The day-by-day history index, built from synthetic GDELT rows (no network)."""

import pandas as pd
import pytest

from src import history


def rows(*events):
    """GDELT-shaped rows: (a1c, a2c, goldstein, quad, articles, event_code)."""
    return pd.DataFrame([
        {"actor1": f"{a1} PARTY", "a1c": a1, "actor2": f"{a2} PARTY" if a2 else None, "a2c": a2, "event_code": code,
         "quad": quad, "goldstein": g, "articles": art, "tone": -1.0, "url": f"https://news.example/{a1}-{code}"}
        for a1, a2, g, quad, art, code in events
    ])


def test_a_country_counts_once_per_event_whether_actor1_or_actor2():
    days, events = history.aggregate(rows(
        ("NZL", "AUS", -10.0, 4, 30, "190"),
        ("AUS", "NZL", 3.0, 1, 5, "040"),
        ("NZL", "NZL", 1.0, 1, 2, "010"),   # both actors: counted once for NZL
        ("USA", None, -2.0, 3, 1, "111"),
    ))
    nzl = days.set_index("country").loc["NZL"]
    assert nzl["events"] == 3
    assert nzl["avg_goldstein"] == pytest.approx((-10 + 3 + 1) / 3, abs=1e-3)
    assert nzl["min_goldstein"] == -10 and nzl["max_goldstein"] == 3
    assert nzl["conflict_share"] == pytest.approx(1 / 3, abs=1e-3)
    assert nzl["worst_articles"] == 30
    worst = events[(events["country"] == "NZL") & (events["kind"] == "conflict")]
    assert worst.iloc[0]["goldstein"] == -10.0  # most severe first


def make_index(tmp_path, days):
    index = history.HistoryIndex(root=tmp_path)
    for day, frame in days.items():
        index.add(day, *history.aggregate(frame))
    return index


def quiet_day(n=20, g=2.0):
    return rows(*[("NZL", "AUS", g, 1, 1, "040")] * n)


def test_extremes_ranks_days_by_mean_goldstein_and_ignores_thin_days(tmp_path):
    index = make_index(tmp_path, {
        "20240101": quiet_day(),
        "20240102": rows(*[("NZL", "CHN", -6.0, 4, 3, "180")] * 15, *[("NZL", "AUS", 1.0, 1, 1, "040")] * 5),
        "20240103": rows(("NZL", "FJI", -10.0, 4, 99, "190")),            # one event: too thin to rank by mean
        "20240104": quiet_day(g=3.0),
    })
    result = history.extremes("NZL", "20240101", "20240104", "conflict", limit=2, index=index)
    assert result["coverage"]["days_indexed"] == 4 and result["coverage"]["complete"]
    assert [d["date"] for d in result["days"]] == ["20240102", "20240101"]
    assert result["days"][0]["avg_goldstein"] == pytest.approx(-4.25, abs=1e-3)
    assert result["days"][0]["top_events"][0]["event_type"] == "Assault"
    # ...but the single most severe event is still found, on the thin day.
    assert result["severe_events"][0]["date"] == "20240103"
    assert result["severe_events"][0]["num_articles"] == 99
    assert result["series"] and result["baseline"]["days_with_events"] == 4


def test_cooperation_ranks_the_other_way(tmp_path):
    index = make_index(tmp_path, {"20240101": quiet_day(g=1.0), "20240102": quiet_day(g=7.0)})
    result = history.extremes("NZL", "20240101", "20240102", "cooperation", index=index)
    assert result["days"][0]["date"] == "20240102"


def test_coverage_reports_a_partial_index(tmp_path):
    index = make_index(tmp_path, {"20240105": quiet_day()})
    result = history.extremes("NZL", "20240101", "20240110", index=index)
    assert result["coverage"]["days_in_range"] == 10
    assert result["coverage"]["days_indexed"] == 1
    assert not result["coverage"]["complete"]


def test_an_unknown_country_returns_empty_lists_not_an_error(tmp_path):
    index = make_index(tmp_path, {"20240101": quiet_day()})
    result = history.extremes("XYZ", "20240101", "20240101", index=index)
    assert result["days"] == [] and result["severe_events"] == []


def test_days_older_than_the_window_are_dropped_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORY_DAYS", "30")
    make_index(tmp_path, {"20000101": quiet_day()})
    reloaded = history.HistoryIndex(root=tmp_path)
    reloaded.load()
    assert reloaded.dates == set()
    assert not (tmp_path / "days" / "20000101.parquet").exists()


def test_index_reloads_from_disk(tmp_path):
    make_index(tmp_path, {"20240101": quiet_day(), "20240102": quiet_day(g=-3.0)})
    reloaded = history.HistoryIndex(root=tmp_path, start=__import__("datetime").date(2024, 1, 1))
    reloaded.load()
    assert reloaded.dates == {"20240101", "20240102"}
    assert history.extremes("NZL", "20240101", "20240102", index=reloaded)["days"][0]["date"] == "20240102"


# ------------------------------------------------------------------ the backfile
def archive_line(day, a1, a2, g, quad, articles, code):
    """One 57-column GDELT 1.0 backfile row (no source URL)."""
    cols = [""] * 57
    cols[0], cols[1] = "1", day
    cols[6], cols[7], cols[16], cols[17] = f"{a1} PARTY", a1, f"{a2} PARTY", a2
    cols[26], cols[29], cols[30], cols[33], cols[34] = code, str(quad), str(g), str(articles), "-2.0"
    return "\t".join(cols)


def test_archive_rows_are_grouped_by_their_own_date(tmp_path):
    import zipfile
    lines = [archive_line("20020301", "NZL", "AUS", -10.0, 4, 12, "190"),
             archive_line("20020301", "NZL", "AUS", 2.0, 1, 1, "040"),
             archive_line("20020302", "NZL", "FJI", 5.0, 1, 3, "050"),
             archive_line("20020415", "NZL", "AUS", 1.0, 1, 1, "040")]   # outside the period asked for
    path = tmp_path / "200203.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("200203.csv", "\n".join(lines) + "\n")
    built = history.aggregate_archive(path, history.archive_period("200203"))
    assert sorted(built) == ["20020301", "20020302"]
    days, events = built["20020301"]
    nzl = days.set_index("country").loc["NZL"]
    assert nzl["events"] == 2 and nzl["min_goldstein"] == -10
    worst = events[(events["country"] == "NZL") & (events["kind"] == "conflict")].iloc[0]
    assert worst["goldstein"] == -10 and worst["url"] is None   # the backfile has no source URLs


def test_archive_names_and_periods():
    from datetime import date
    assert history.archive_name(date(2003, 7, 9)) == "2003"
    assert history.archive_name(date(2009, 6, 30)) == "200906"
    assert history.archive_period("2003") == ("20030101", "20031231")
    assert history.archive_period("200902") == ("20090201", "20090228")
    assert history.archive_period("201212") == ("20121201", "20121231")


def test_each_day_is_judged_against_its_own_years_volume(tmp_path):
    # 2002 was a thin year in GDELT: 8 events a day was normal then, and would be
    # far too few to rank in 2022. Both years' worst days are eligible.
    index = make_index(tmp_path, {
        "20020101": rows(*[("NZL", "AUS", 2.0, 1, 1, "040")] * 8),
        "20020102": rows(*[("NZL", "AUS", -5.0, 4, 1, "180")] * 8),
        "20220101": rows(*[("NZL", "AUS", 2.0, 1, 1, "040")] * 400),
        "20220102": rows(*[("NZL", "AUS", -1.0, 3, 1, "111")] * 400),
    })
    result = history.extremes("NZL", "20020101", "20221231", "conflict", limit=2, index=index)
    assert [d["date"] for d in result["days"]] == ["20020102", "20220102"]
    assert result["coverage"]["before_daily_files"] is True
