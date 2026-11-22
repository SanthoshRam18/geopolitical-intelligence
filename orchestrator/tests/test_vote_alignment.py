"""'Which country agrees most with Iran on human rights': UN votes compared, by topic."""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from app import intent
from app.pipeline import Orchestrator
from tests.replay import ReplayTransport, load_records

ROOT = Path(__file__).resolve().parent.parent
RECORDS = load_records()
QUESTION = "which country agrees with most with iran on human rights"


# ------------------------------------------------------------------ reading
@pytest.mark.parametrize("question, country, topic", [
    (QUESTION, "IRN", "human rights"),
    ("Which countries vote most like India at the UN?", "IND", None),
    ("who votes with Russia on Ukraine since 2014", "RUS", "ukraine"),
    ("Who disagrees most with Israel on Palestine?", "ISR", "palestine"),
    ("which countries side with China on nuclear weapons", "CHN", "nuclear weapons"),
])
def test_agreement_questions_compare_un_votes_with_policy_stance_first(question, country, topic):
    plan = intent.parse(question)
    assert plan.intent == "vote_alignment"
    assert plan.iso3s[0] == country and plan.topic == topic
    assert plan.agent_order[0] == "policy_stance"


@pytest.mark.parametrize("question, expected", [
    ("Who does Vietnam side with?", "blocs"),
    ("Is Vietnam aligned with any bloc?", "blocs"),
    ("India and China relations", "bilateral"),
])
def test_bloc_and_relationship_questions_keep_their_reading(question, expected):
    assert intent.parse(question).intent == expected


def test_since_a_year_limits_the_votes():
    plan = intent.parse("who votes with Russia on Ukraine since 2014")
    assert plan.time.mode == "range" and plan.time.start.startswith("2014")


# ------------------------------------------------------------------ answering
PAYLOAD = {
    "country": "Iran", "topic": "human rights", "terms": ["human rights"], "resolutions": 412, "years": [1989, 2025],
    "own_votes": {"yes": 180, "no": 190, "abstain": 42},
    "top_topics": [{"topic": "HUMAN RIGHTS--REPORTS|HUMAN RIGHTS--IRAN (ISLAMIC REPUBLIC OF)", "count": 30}],
    "countries_compared": 190, "min_shared": 10,
    "most_aligned": [{"country": "Syria", "agreement": 0.93, "shared": 400, "opposed": 3}, {"country": "Cuba", "agreement": 0.91, "shared": 405, "opposed": 4}],
    "least_aligned": [{"country": "Canada", "agreement": 0.12, "shared": 410, "opposed": 300}],
    "pairs": [],
}


class AlignmentTransport(ReplayTransport):
    def __init__(self):
        super().__init__(RECORDS)
        self.asked = []

    async def handle_async_request(self, request):
        if request.url.path == "/topic-agreement":
            self.asked.append(dict(request.url.params))
            return httpx.Response(200, json=PAYLOAD, request=request)
        return await super().handle_async_request(request)


def ask(question, overrides):
    transport = AlignmentTransport()

    async def run():
        orchestrator = Orchestrator(client=httpx.AsyncClient(transport=transport))
        try:
            return await orchestrator.ask(question, overrides)
        finally:
            await orchestrator.close()
    return asyncio.run(run()), transport


def test_the_answer_ranks_countries_by_identical_votes_on_the_topic():
    result, transport = ask(QUESTION, {"agents": ["policy_stance"]})
    assert transport.asked and transport.asked[0]["topic"] == "human rights"
    insights = result["agents"]["policy_stance"]["envelope"]["insights"]
    assert [i["facet"] for i in insights[:3]] == ["vote_alignment", "vote_opposition", "topic_stance"]
    assert "voted most often like Iran are Syria (93%), Cuba (91%)" in insights[0]["claim"]
    assert "412 resolutions, 1989-2025" in insights[0]["claim"]
    brief = result["briefing"]
    assert brief["headline"] == "Iran: who votes with it at the UN on human rights"
    assert brief["summary"][0].startswith("On UN General Assembly votes about human rights")


def test_the_page_shows_the_vote_ranking():
    if not shutil.which("node"):
        pytest.skip("node is needed to run the UI script")
    result, _ = ask(QUESTION, {"agents": ["policy_stance"]})
    out = subprocess.run(["node", str(ROOT / "tests" / "ui_harness.js"), str(ROOT / "static" / "index.html"), "Policy Stance"],
                         input=json.dumps(result), capture_output=True, text=True, encoding="utf-8", check=True)
    page = json.loads(out.stdout)
    assert "Vote most like Iran on human rights" in page["headings"]
    assert "Vote least like Iran on human rights" in page["headings"]
