"""Questions that search the day-by-day GDELT history ("which day ... most
conflictual ... in the last 5 years"): how they are read, what the Events
adapter asks for and says, and what the page shows."""

import asyncio
import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app import intent
from app.pipeline import Orchestrator

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ reading
@pytest.mark.parametrize("question, country, direction", [
    ("which day did new zealand have a very conflicting event or horrible score in the last 5 years", "NZL", "conflict"),
    ("the date at which New Zealand had the most conflicting event in last 5 years", "NZL", "conflict"),
    ("When was India most hostile in the past 2 years?", "IND", "conflict"),
    ("Most cooperative day for Japan since 2022", "JPN", "cooperation"),
    ("worst days for Iran in 2024", "IRN", "conflict"),
    ("least conflictual day for Germany in the past year", "DEU", "cooperation"),
])
def test_history_questions_go_to_the_events_history_only(question, country, direction):
    plan = intent.parse(question)
    assert plan.intent == "event_history"
    assert plan.iso3s[0] == country
    assert plan.direction == direction
    # Events holds the history, so it is the most relevant and runs first; the others add context.
    assert plan.agent_order[0] == "event_summarization"
    assert "day-by-day history" in plan.lead_reason
    assert set(plan.agents) == {"event_summarization", "policy_stance", "soft_power", "trade_intelligence"}
    assert plan.time.mode == "range" and plan.time.start <= plan.time.end


def test_ranges_are_read_from_the_wording():
    yesterday = date.today() - timedelta(days=1)
    five = intent.parse("When was New Zealand most hostile in the last five years?").time
    assert five.end == yesterday.strftime("%Y%m%d")
    assert five.start == (yesterday - timedelta(days=5 * 365 - 1)).strftime("%Y%m%d")
    assert five.label == "the last 5 years"
    assert intent.parse("worst days for Iran in 2024").time.start == "20240101"
    assert intent.parse("worst days for Iran in 2024").time.end == "20241231"
    assert intent.parse("Most cooperative day for Japan since 2022").time.start == "20220101"
    between = intent.parse("most violent day for Mexico between 2023 and 2024").time
    assert (between.start, between.end) == ("20230101", "20241231")


def test_a_history_question_without_a_range_searches_the_last_year_and_says_so():
    plan = intent.parse("Which day was France most tense?")
    assert plan.intent == "event_history"
    assert any("last year" in note for note in plan.notes)


@pytest.mark.parametrize("question, expected", [
    ("How exposed is India right now?", "country_profile"),
    ("What happened in Iran yesterday?", "events"),
    ("What is the conflict record of Russia?", "country_profile"),
    ("Which countries are most dependent on trade?", "ranking"),
    ("Forecast Brazil exports to 2030", "forecast"),
    ("How conflictual are Russia and Ukraine?", "bilateral"),
])
def test_other_questions_keep_their_reading(question, expected):
    assert intent.parse(question).intent == expected


# ------------------------------------------------------------------ answering
def history_payload(start, end, complete=False):
    def event(day, actor1, kind, actor2, g, n):
        return {"date": day, "actor1": actor1, "actor2": actor2, "event_type": kind, "goldstein": g, "num_articles": n, "url": f"https://news.example/{day}"}
    return {
        "country_code": "NZL", "start": start, "end": end, "direction": "conflict", "min_events": 40,
        "coverage": {"days_in_range": 1826, "days_indexed": 1826 if complete else 900, "days_missing_upstream": 0,
                     "first": "20240601", "last": end, "complete": complete},
        "days": [
            {"date": "20250315", "events": 212, "avg_goldstein": -3.41, "min_goldstein": -10.0, "conflict_share": 0.52,
             "top_events": [event("20250315", "Gunman", "Mass Violence", "New Zealand", -10.0, 140)]},
            {"date": "20240911", "events": 180, "avg_goldstein": -2.1, "min_goldstein": -10.0, "conflict_share": 0.41, "top_events": []},
        ],
        "severe_events": [event("20250315", "Gunman", "Mass Violence", "New Zealand", -10.0, 140)],
        "baseline": {"avg_goldstein": 1.2, "events_per_day": 410.0, "days_with_events": 900, "conflict_share": 0.18},
        "series": [{"start": "20240603", "avg_goldstein": 1.1, "events": 2800}, {"start": "20240610", "avg_goldstein": -0.4, "events": 3100}],
        "series_period": "week",
    }


class HistoryTransport(httpx.AsyncBaseTransport):
    """Answers only the Events history call; records what was asked."""

    def __init__(self, status=200, complete=False):
        self.status, self.complete, self.asked = status, complete, []

    async def handle_async_request(self, request):
        if request.url.path == "/history/extremes":
            params = dict(request.url.params)
            self.asked.append(params)
            if self.status != 200:
                return httpx.Response(self.status, json={"detail": "Not Found"}, request=request)
            return httpx.Response(200, json=history_payload(params["start"], params["end"], self.complete), request=request)
        return httpx.Response(404, json={"detail": "not used"}, request=request)


def ask(question, transport):
    async def run():
        orchestrator = Orchestrator(client=httpx.AsyncClient(transport=transport))
        try:
            return await orchestrator.ask(question)
        finally:
            await orchestrator.close()
    return asyncio.run(run())


QUESTION = "which day did new zealand have a very conflicting event or horrible score in the last 5 years"


def test_the_answer_names_the_days_and_cites_the_events():
    transport = HistoryTransport()
    result = ask(QUESTION, transport)
    assert transport.asked[0]["country_code"] == "NZL" and transport.asked[0]["direction"] == "conflict"
    events = result["agents"]["event_summarization"]
    assert events["status"] == "ok"
    insights = events["envelope"]["insights"]
    assert [i["facet"] for i in insights] == ["event_extreme_day", "event_extreme_day", "event_extreme_events", "event_history_baseline"]
    first = insights[0]["claim"]
    assert first.startswith("New Zealand's most conflictual day in the last 5 years (")
    assert "was 2025-03-15" in first and "Goldstein -3.41" in first and "Gunman - Mass Violence - New Zealand" in first
    # Half the range is indexed: said, and the confidence reflects it.
    assert "covers 900 of 1,826 days" in insights[0]["caveat"]
    assert any("still being built" in note for note in events["notes"])

    brief = result["briefing"]
    assert brief["headline"] == "New Zealand: most conflictual days in the last 5 years"
    assert brief["summary"][0].startswith("New Zealand's most conflictual day")
    assert brief["summary"][1].startswith("#2 most conflictual day: 2024-09-11")


def test_a_complete_index_has_no_coverage_caveat_and_higher_confidence():
    partial = ask(QUESTION, HistoryTransport())["agents"]["event_summarization"]["envelope"]["insights"][0]
    complete = ask(QUESTION, HistoryTransport(complete=True))["agents"]["event_summarization"]["envelope"]["insights"][0]
    assert complete.get("caveat") is None
    assert complete["confidence"] > partial["confidence"]


def test_an_events_service_without_history_says_so_instead_of_failing():
    result = ask(QUESTION, HistoryTransport(status=404))
    events = result["agents"]["event_summarization"]
    assert events["status"] == "ok" and events["insight_count"] == 0
    assert any("no day-by-day history index" in note for note in events["notes"])


def test_the_page_shows_the_ranked_days():
    if not shutil.which("node"):
        pytest.skip("node is needed to run the UI script")
    result = ask(QUESTION, HistoryTransport())
    out = subprocess.run(["node", str(ROOT / "tests" / "ui_harness.js"), str(ROOT / "static" / "index.html"), "Events"],
                         input=json.dumps(result), capture_output=True, text=True, encoding="utf-8", check=True)
    page = json.loads(out.stdout)
    assert "The most conflictual days" in page["headings"]
    assert "Most severe single events" in page["headings"]
    assert "2025-03-15" in page["text"]


def test_ranges_older_than_the_five_year_history_are_cut_and_say_so():
    first = (date.today() - timedelta(days=1) - timedelta(days=intent.HISTORY_DAYS - 1)).strftime("%Y%m%d")
    plan = intent.parse("most violent day for Iraq since 2003")
    assert plan.intent == "event_history" and plan.time.start == first
    assert "covers the last 5 years" in plan.time.label
