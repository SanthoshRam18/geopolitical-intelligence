"""Intent parsing: a natural-language question becomes a query plan.

The parser is deliberately rule-based. Every decision it makes is inspectable in
the plan it returns (``/parse`` exposes it on its own), it needs no API key, and it
behaves identically in a demo and in a test. A question it cannot place falls back
to a country profile or a global overview rather than failing.

Before parsing, the question is reformulated (reformulate.py): aliases, typos,
number words and unfamiliar phrasings are rewritten into words the parser knows.
The plan keeps the original question, the text it actually read, and every
rewrite.

A plan answers four questions:
    intent    what kind of question is this
    entities  which countries, as ISO3
    time      which year or day
    agents    which agents to ask, and why

and carries an ``understanding`` confidence: how sure the system is that it read
the question as meant. It falls with each rewrite and when the plan looks like a
misreading, such as no country found in a question that is not a global one.
It says nothing about the data; claim confidences do that.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from . import countries, reformulate
from .config import REFORMULATION_STEPS

INTENTS = ("country_profile", "bilateral", "shock", "forecast", "blocs", "events", "ranking", "event_history", "vote_alignment")
ALL_AGENTS = ("soft_power", "policy_stance", "trade_intelligence", "event_summarization")

_SHOCK = re.compile(
    r"\b(what if|stops?|stopped|halts?|halted|cuts? off|embargo(?:es|ed)?|blockades?|bans?|banned|sanction(?:s|ed)?|"
    r"disrupt(?:s|ed|ion)?|shock|collapse[sd]?|shuts? down|restricts?|restrictions?)\b",
    re.I,
)
_FORECAST = re.compile(r"\b(forecast|predict(?:ion|ed)?|projection|project(?:ed)?|outlook|future|next \d+ years|by 20[3-9]\d|trajectory|will .* (?:grow|rise|fall|decline))\b", re.I)
_BLOCS = re.compile(r"\b(blocs?|alliances?|allied|aligned|alignment|camps?|non-aligned|side with|coalition)\b", re.I)
_EVENTS = re.compile(r"\b(today|yesterday|news|headlines?|happening|this week|latest events|recent events|events)\b", re.I)
_RANKING = re.compile(r"\b(which countries|what countries|who are|top \d+|most|least|rank(?:ed|ing)?|leading|largest|biggest)\b", re.I)
_BILATERAL = re.compile(r"\b(relations?|relationship|ties|between|versus|vs\.?|compare[ds]?|comparison|bilateral|dependence on|depend on)\b", re.I)

# "Which day / when ... most conflictual ... in the last five years": a search of the
# day-by-day GDELT history for the extreme days, not a profile of the country.
_HISTORY_WHEN = re.compile(r"\b(?:which|what) (?:day|date|days|dates|week|weeks|month|months)\b|\bwhen (?:did|was|were|has|had|have)\b|"
                           r"\bon what (?:day|date)\b|\b(?:days?|dates?) (?:when|on which|where|with)\b", re.I)
_HISTORY_EXTREME = re.compile(r"\b(most|worst|highest|lowest|biggest|peak|maximum|minimum|very|extremely|horrible|terrible|awful|severe|bad|best|least|calmest)\b", re.I)
_CONFLICTUAL = re.compile(r"\b(conflict\w*|hostil\w*|violen\w*|tense|tensions?|negative|aggress\w*|clash\w*|attacks?|fight\w*|horrible|terrible|awful|"
                          r"worst|severe|bad|turbulent|unrest|crisis|crises)\b", re.I)
_COOPERATIVE = re.compile(r"\b(cooperat\w*|co-operat\w*|friendl\w*|positive|peaceful|calm\w*|best|warmest|cordial)\b", re.I)
_HISTORY_MEASURE = re.compile(r"\b(goldstein|scores?|tone)\b", re.I)
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
_RELATIVE_RANGE = re.compile(r"\b(?:last|past|previous|preceding|recent)\s+(\d{1,4}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty)?\s*"
                             r"(day|week|month|year)s?\b", re.I)
_SINCE_YEAR = re.compile(r"\bsince (19\d{2}|20\d{2})\b", re.I)
_BETWEEN_YEARS = re.compile(r"\b(?:between|from) (19\d{2}|20\d{2}) (?:and|to|until|-) (19\d{2}|20\d{2})\b", re.I)
_IN_YEAR = re.compile(r"\b(?:in|during|throughout|of) (19\d{2}|20\d{2})\b", re.I)
HISTORY_DAYS = 1830   # the Events day-by-day history covers the last five years (its HISTORY_DAYS)
DEFAULT_HISTORY_DAYS = 365

# "Which country agrees most with Iran on human rights": a ranking of every country
# by how often it voted like Iran in the UN General Assembly, on that topic.
_AGREEMENT = re.compile(r"\b(agree\w*|agreement|disagree\w*|vot(?:e|es|ed|ing)\s+(?:(?:most|more|mostly|the\s+most|least)\s+)?(?:with|like|alongside|against|the same)|voting partners?|"
                        r"sides?\s+with|sided\s+with|align\w*\s+with|stands?\s+with|in line with|closest\s+(?:to|ally|allies|partner)|allies\s+on)\b", re.I)
_TOPIC_PHRASE = re.compile(r"\b(?:on|about|over|regarding|concerning|when it comes to|in terms of|for)\s+(?:the\s+)?(?:issues?\s+of\s+|questions?\s+of\s+|topics?\s+of\s+|matter\s+of\s+)?"
                           r"([a-z][a-z' \-]{2,40}?)\s*(?=$|[?.,;]|\b(?:at|in)\s+the\s+un\b|\bat\s+un\b|\bsince\b|\bbetween\b|\bfrom\b|\bduring\b|\bin\s+(?:19|20)\d\d|"
                           r"\bresolutions?\b|\bvotes?\b|\bissues?\b|\bmatters?\b|\bquestions?\b|\bin the (?:last|past))", re.I)
# A topic as said -> the terms the UN vote topics and titles use ("|" = any of them).
_TOPIC_TERMS = {
    "human rights": "human rights", "rights": "human rights", "nuclear": "nuclear", "nukes": "nuclear", "nuclear weapons": "nuclear",
    "disarmament": "disarmament", "weapons": "weapons|arms|disarmament", "arms": "arms|weapons", "israel": "israel", "palestine": "palestin",
    "palestinians": "palestin", "gaza": "palestin|israel", "middle east": "middle east", "women": "women", "gender": "women",
    "climate": "climate|environment|sustainable", "climate change": "climate|environment|sustainable", "environment": "environment|sustainable|climate",
    "colonialism": "decoloni|self-determination|non-self-governing", "decolonization": "decoloni", "racism": "racial|apartheid",
    "refugees": "refugee", "terrorism": "terroris", "sanctions": "sanctions", "ukraine": "ukraine", "russia": "russia|ukraine",
    "space": "outer space", "outer space": "outer space", "development": "development", "trade": "trade", "cuba": "cuba",
    "self-determination": "self-determination", "death penalty": "capital punishment", "capital punishment": "capital punishment",
    "children": "child", "democracy": "democracy", "chemical weapons": "chemical", "landmines": "landmines|mines",
}
_VOTE_WORDS = re.compile(r"\b(agree\w*|agreement|disagree\w*|vot(?:e|es|ed|ing)|un votes?|at the un|in the un|general assembly)\b", re.I)
_BLOC_WORDS = re.compile(r"\b(blocs?|alliances?|camps?|coalitions?|non-aligned)\b", re.I)
_TOPIC_NOISE = re.compile(r"\b(the|un|united nations|general assembly|ga|issues?|matters?|questions?|topics?|policy|policies)\b", re.I)

_DOMAIN = {
    "trade_intelligence": re.compile(r"\b(trade|trading|exports?|imports?|supply|suppliers?|tariffs?|economic|economy|dependen\w*|leverage|fragil\w*|sectors?)\b", re.I),
    "soft_power": re.compile(r"\b(soft powers?|influence|reputation|culture|cultural|attractiveness|image|brand|prestige)\b", re.I),
    "policy_stance": re.compile(r"\b(un |united nations|vot(?:e|es|ing)|diplomac\w*|diplomatic|conflicts?|wars?|alliances?|blocs?|stance|policy)\b", re.I),
    "event_summarization": re.compile(r"\b(news|events?|today|yesterday|happening|headlines?|right now|currently|this week)\b", re.I),
}

_SECTORS = (
    ("energy", re.compile(r"\b(energy|oil|gas|lng|fuel|petroleum|coal|crude)\b", re.I)),
    ("agriculture", re.compile(r"\b(agri\w*|food|grain|wheat|rice|crops?|fertili[sz]ers?)\b", re.I)),
    ("electronics", re.compile(r"\b(electronics?|chips?|semiconductors?|microchips?|tech(?:nology)?)\b", re.I)),
)


@dataclass
class Entity:
    iso3: str
    name: str
    matched: str


@dataclass
class TimeWindow:
    mode: str = "latest"          # latest | year | date | range
    year: Optional[int] = None
    date: Optional[str] = None    # YYYYMMDD
    horizon_year: Optional[int] = None
    start: Optional[str] = None   # YYYYMMDD, for a range
    end: Optional[str] = None     # YYYYMMDD, for a range
    label: Optional[str] = None   # the range as asked: "the last 5 years"


@dataclass
class QueryPlan:
    question: str
    intent: str
    entities: list[Entity]
    time: TimeWindow
    sector: str = "all"
    severity: Optional[float] = None
    metric: str = "exports"
    limit: int = 5
    trade_query: Optional[str] = None
    direction: Optional[str] = None      # event_history: "conflict" or "cooperation"
    topic: Optional[str] = None          # vote_alignment: the topic as said ("human rights")
    topic_terms: Optional[str] = None    # vote_alignment: '|'-separated terms matched in UN vote topics
    agents: list[str] = field(default_factory=list)
    agent_reasons: dict[str, str] = field(default_factory=dict)
    focus_agents: list[str] = field(default_factory=list)
    # The engaged agents, most relevant to this question first (_rank_agents).
    agent_order: list[str] = field(default_factory=list)
    agent_relevance: dict[str, float] = field(default_factory=dict)
    lead_reason: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    read_as: Optional[str] = None        # the reformulated question, when it differs
    rewrites: list[dict] = field(default_factory=list)
    understanding: float = 1.0

    @property
    def iso3s(self) -> list[str]:
        return [entity.iso3 for entity in self.entities]

    @property
    def primary(self) -> Optional[str]:
        return self.entities[0].iso3 if self.entities else None

    def to_dict(self) -> dict:
        return asdict(self)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_time(question: str, intent_hint: str) -> TimeWindow:
    iso_date = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", question)
    if iso_date:
        try:
            parsed = date(int(iso_date.group(1)), int(iso_date.group(2)), int(iso_date.group(3)))
            return TimeWindow(mode="date", date=parsed.strftime("%Y%m%d"), year=parsed.year)
        except ValueError:
            pass
    if re.search(r"\byesterday\b", question, re.I):
        day = _today() - timedelta(days=1)
        return TimeWindow(mode="date", date=day.strftime("%Y%m%d"), year=day.year)

    years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", question)]
    if years:
        future = [y for y in years if y > _today().year - 1]
        past = [y for y in years if y <= _today().year - 1]
        if future and intent_hint == "forecast":
            return TimeWindow(mode="latest", horizon_year=max(future))
        if past:
            return TimeWindow(mode="year", year=past[0])
    return TimeWindow(mode="latest")


def _parse_range(question: str, history: bool = True) -> tuple[TimeWindow, bool]:
    """A date range for a history search, and whether the question gave one.

    "the last 5 years", "past 6 months", "since 2022", "between 2019 and 2021",
    "in 2023", "this year", "last year". Without one: the last year. The range
    ends yesterday (GDELT publishes a day after it) and starts no earlier than
    GDELT's first daily export. ``history=False`` (UN votes, which go back to 1946)
    skips the Events history's five-year limit."""
    yesterday = _today() - timedelta(days=1)
    first_day = yesterday - timedelta(days=HISTORY_DAYS - 1) if history else date(1946, 1, 1)

    def window(start: date, end: date, label: str) -> TimeWindow:
        end = min(end, yesterday)
        if start < first_day:
            label = f"{label} (the history covers the last 5 years)" if history else label
        start = max(start, first_day)
        if start > end:
            start = end
        return TimeWindow(mode="range", start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"), label=label)

    match = _BETWEEN_YEARS.search(question)
    if match:
        a, b = sorted((int(match.group(1)), int(match.group(2))))
        return window(date(a, 1, 1), date(b, 12, 31), f"{a}-{b}"), True
    match = _SINCE_YEAR.search(question)
    if match:
        year = int(match.group(1))
        return window(date(year, 1, 1), yesterday, f"since {year}"), True
    match = _RELATIVE_RANGE.search(question)
    if match:
        raw, unit = (match.group(1) or "").lower(), match.group(2).lower()
        # Bare "last year" is the previous calendar year; "the last year" and
        # "the past year" are the last twelve months.
        if not raw and unit == "year" and re.search(r"(?<!the )\blast year\b", question, re.I):
            previous = yesterday.year - 1
            return window(date(previous, 1, 1), date(previous, 12, 31), str(previous)), True
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, 1)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * max(1, n)
        label = f"the last {unit}" if n == 1 else f"the last {n} {unit}s"
        return window(yesterday - timedelta(days=days - 1), yesterday, label), True
    if re.search(r"\bthis year\b", question, re.I):
        return window(date(yesterday.year, 1, 1), yesterday, "this year"), True
    if re.search(r"\bthis month\b", question, re.I):
        return window(yesterday.replace(day=1), yesterday, "this month"), True
    match = _IN_YEAR.search(question)
    if match:
        year = int(match.group(1))
        return window(date(year, 1, 1), date(year, 12, 31), str(year)), True
    return window(yesterday - timedelta(days=DEFAULT_HISTORY_DAYS - 1), yesterday, "the last year"), False


def _is_history_question(question: str, entities: list[Entity]) -> bool:
    """A search across days for the extreme ones, rather than a profile."""
    if not entities:
        return False
    if not (_CONFLICTUAL.search(question) or _COOPERATIVE.search(question) or _HISTORY_MEASURE.search(question)):
        return False
    if _HISTORY_WHEN.search(question):
        return True
    has_range = bool(_RELATIVE_RANGE.search(question) or _SINCE_YEAR.search(question) or _BETWEEN_YEARS.search(question))
    names_a_day = re.search(r"\b(days?|dates?|weeks?|months?)\b", question, re.I)
    return bool(_HISTORY_EXTREME.search(question) and (has_range or names_a_day))


def _is_vote_alignment(question: str, entities: list[Entity]) -> bool:
    """Who votes like a country at the UN (optionally on a topic).

    "Agree" and "vote with" always mean UN votes. "Side with" and "aligned with"
    mean them only when a topic is named and no bloc is: "who does Vietnam side
    with?" asks for its bloc."""
    if not entities or _SHOCK.search(question) or not _AGREEMENT.search(question):
        return False
    if _VOTE_WORDS.search(question):
        return True
    return not _BLOC_WORDS.search(question) and _vote_topic(question, entities)[0] is not None


def _vote_topic(question: str, entities: list[Entity]) -> tuple[Optional[str], Optional[str]]:
    """(the topic as said, the terms to match in UN vote topics) or (None, None) for all votes."""
    # The first country is the one whose votes are compared; another country named
    # after "on" is the topic ("votes with Russia on Ukraine").
    subject = {entities[0].name.lower(), entities[0].matched.lower()} if entities else set()
    for match in _TOPIC_PHRASE.finditer(question):
        phrase = _TOPIC_NOISE.sub(" ", match.group(1).lower())
        phrase = re.sub(r"\s+", " ", phrase).strip(" -'")
        if not phrase or phrase in subject or len(phrase) < 3:
            continue
        terms = _TOPIC_TERMS.get(phrase) or next((v for k, v in _TOPIC_TERMS.items() if re.search(rf"\b{re.escape(k)}\b", phrase)), None)
        return phrase, terms or phrase
    return None, None


def _direction(question: str) -> str:
    """Whether a history search wants the most conflictual or the most cooperative days."""
    if re.search(r"\b(?:least|lowest) (?:conflict\w*|hostil\w*|tense|violen\w*|negative)", question, re.I):
        return "cooperation"
    if _COOPERATIVE.search(question) and not _CONFLICTUAL.search(question):
        return "cooperation"
    return "conflict"


def _severity(question: str) -> float:
    pct = re.search(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)", question, re.I)
    if pct:
        return max(0.01, min(1.0, float(pct.group(1)) / 100.0))
    if re.search(r"\b(halve[sd]?|half)\b", question, re.I):
        return 0.5
    if re.search(r"\b(stops?|stopped|halts?|halted|cuts? off|embargo\w*|blockades?|bans?|banned|shuts? down|total|complete)\b", question, re.I):
        return 1.0
    return 0.5


def _sector(question: str) -> str:
    for name, pattern in _SECTORS:
        if pattern.search(question):
            return name
    return "all"


def _trade_ranking_query(question: str) -> str:
    if re.search(r"\b(bloc|blocs|alliance|camp)\b", question, re.I):
        return "blocs"
    if re.search(r"\b(dependen\w*|leverage|reliant|rely)\b", question, re.I):
        return "leverage"
    if re.search(r"\b(fragil\w*|substitut\w*|supply chains?|brittle)\b", question, re.I):
        return "fragility"
    return "risk"


def _choose_intent(question: str, entities: list[Entity]) -> str:
    n = len(entities)
    if _is_history_question(question, entities):
        return "event_history"
    if _is_vote_alignment(question, entities):
        return "vote_alignment"
    if n >= 1 and _SHOCK.search(question):
        return "shock"
    if n >= 1 and _FORECAST.search(question):
        return "forecast"
    if n >= 2:
        return "bilateral"
    if _BLOCS.search(question):
        return "blocs"
    if n == 1 and _EVENTS.search(question) and not re.search(r"\b(exposed|exposure|profile|overall|position)\b", question, re.I):
        return "events"
    if n == 0:
        return "ranking"
    return "country_profile"


_INTENT_AGENTS = {
    "country_profile": {
        "soft_power": "influence level, trend, drivers and forecast",
        "policy_stance": "UN-voting bloc, conflict record, diplomatic partners",
        "trade_intelligence": "structural risk, trade bloc, leverage, sector fragility",
        "event_summarization": "latest day of GDELT activity and counterparts",
    },
    "bilateral": {
        "soft_power": "influence of each side",
        "policy_stance": "UN-vote similarity and bloc of each side",
        "trade_intelligence": "trade bloc and dependence of each side",
        "event_summarization": "latest interactions and 1990-2024 relationship baseline",
    },
    "shock": {
        "trade_intelligence": "propagate the export disruption through the trade graph",
        "policy_stance": "diplomatic bloc of the origin and of the most exposed economies",
        "event_summarization": "what the news shows about the origin right now",
    },
    "forecast": {
        "trade_intelligence": "trade projection with automatic model selection",
        "soft_power": "5-year Kalman forecast of the influence score",
        "policy_stance": "conflict-activity trend",
    },
    "blocs": {
        "trade_intelligence": "Louvain trading blocs",
        "policy_stance": "UN-voting alliance blocs",
        "soft_power": "closest influence peers",
    },
    "events": {
        "event_summarization": "GDELT events for the day",
        "policy_stance": "bloc context for the counterparts",
        "trade_intelligence": "trade stakes with the counterparts",
    },
    "event_history": {
        # Events answers the question; the others add what the days alone cannot show.
        "event_summarization": "search the day-by-day GDELT history for the extreme days and events",
        "policy_stance": "conflict record (UCDP) and UN-voting bloc, as context for those days",
        "soft_power": "how its influence moved over the same years",
        "trade_intelligence": "trade exposure and dependence: the economic stakes",
    },
    "vote_alignment": {
        # Policy Stance answers; the others add what the votes alone cannot show.
        "policy_stance": "compare every country's UN General Assembly votes with the target's, on the topic asked",
        "soft_power": "how its closest profiles compare in influence",
        "trade_intelligence": "trade ties and dependence: whether votes follow trade",
        "event_summarization": "what the news shows about it right now",
    },
    "ranking": {
        "trade_intelligence": "network-wide ranking",
        "soft_power": "global soft-power leaderboard",
        "policy_stance": "alliance-bloc overview",
    },
}


# The agent whose own data a kind of question is about, and why. Intents left out
# (country_profile, bilateral, forecast) have no single owner: the wording decides.
_INTENT_OWNER = {
    "event_history": ("event_summarization", "it holds the day-by-day history the question asks about"),
    "events": ("event_summarization", "it tracks what is happening day by day"),
    "shock": ("trade_intelligence", "it propagates the disruption through the trade graph"),
    "ranking": ("trade_intelligence", "it ranks every economy in its network"),
    "blocs": ("policy_stance", "it places countries in UN-voting blocs"),
    "vote_alignment": ("policy_stance", "it holds every UN General Assembly vote, by topic"),
}
_FIELD_NAME = {"trade_intelligence": "trade", "soft_power": "influence", "policy_stance": "diplomacy and conflict", "event_summarization": "news events"}


def _rank_agents(intent: str, text: str, time: TimeWindow, agents: list[str]) -> tuple[dict[str, float], list[str], Optional[str]]:
    """Score how relevant each engaged agent is to this question, most relevant first.

    3 for the agent that owns this kind of question, 2 per word naming an agent's
    field (at most two counted), 1 when the time asked matches the agent's grain
    (a day or a range of days: Events; a year: the annual agents). Ties keep the
    usual agent order."""
    scores = {agent: 0.0 for agent in agents}
    reasons: dict[str, str] = {}
    owner = _INTENT_OWNER.get(intent)
    if owner and owner[0] in scores:
        scores[owner[0]] += 3
        reasons[owner[0]] = owner[1]
    for agent, pattern in _DOMAIN.items():
        if agent in scores:
            words = {m.strip().lower() for m in pattern.findall(text)}
            if words:
                scores[agent] += 2 * min(2, len(words))
                reasons.setdefault(agent, f"the question is about {_FIELD_NAME[agent]} ({', '.join(sorted(words)[:3])})")
    if time.mode in ("date", "range") and "event_summarization" in scores:
        scores["event_summarization"] += 1
    elif time.mode == "year":
        for agent in scores:
            if agent != "event_summarization":
                scores[agent] += 0.5
    order = sorted(agents, key=lambda a: -scores[a])  # stable: ties keep ALL_AGENTS order
    lead = order[0] if order else None
    lead_reason = reasons.get(lead) if lead and scores[lead] > 0 else None
    return scores, order, lead_reason


_COUNTRY_REWRITES = ("capital", "endonym", "abbreviation", "combining_form", "country_spelling")


def _typed(matched: str, reformulation: reformulate.Reformulation) -> str:
    """What the user actually typed for a country that reformulation renamed."""
    for rewrite in reformulation.rewrites:
        if rewrite.kind in _COUNTRY_REWRITES and matched.lower() in rewrite.replacement.lower():
            return rewrite.original
    return matched


def _understanding(plan: QueryPlan, reformulation: reformulate.Reformulation, text: str, overrides: dict) -> float:
    """How sure the system is that the plan is what the question meant (0-1)."""
    value = reformulation.confidence
    if not overrides.get("countries") and not overrides.get("intent"):
        if not plan.entities and plan.intent == "ranking" and not _RANKING.search(text):
            value *= 0.4
            plan.notes.append("No country and no ranking cue recognised, so this may not be the question that was asked.")
        elif len(plan.entities) == 1 and plan.intent != "bilateral" and _BILATERAL.search(text):
            value *= 0.6
            plan.notes.append("The question reads like one about a relationship, but only one country was recognised.")
        elif plan.intent == "bilateral" and len(plan.entities) > 2:
            value *= 0.8
    return round(value, 3)


def parse(question: str, overrides: Optional[dict] = None, steps: Optional[Iterable[str] | str] = None) -> QueryPlan:
    """Turn a question into a query plan. ``overrides`` wins over anything parsed.

    ``steps`` picks the reformulation steps to run first; by default the ones
    in ``REFORMULATION_STEPS`` (all of them, unless configured otherwise).
    """
    overrides = {key: value for key, value in (overrides or {}).items() if value is not None}
    reformulation = reformulate.reformulate(question, REFORMULATION_STEPS if steps is None else steps)
    text = reformulation.text

    mentions = countries.find_mentions(text)
    entities = [Entity(iso3=m.iso3, name=m.name, matched=_typed(m.matched, reformulation)) for m in mentions]
    for iso3 in overrides.get("countries", []) or []:
        resolved = countries.resolve(iso3)
        if resolved and resolved not in {e.iso3 for e in entities}:
            entities.append(Entity(iso3=resolved, name=countries.name_of(resolved), matched=str(iso3)))

    intent = overrides.get("intent") or _choose_intent(text, entities)
    if intent not in INTENTS:
        intent = "country_profile" if entities else "ranking"

    range_given = False
    if intent == "event_history":
        time, range_given = _parse_range(text)
    elif intent == "vote_alignment":
        span, given = _parse_range(text, history=False)
        time = span if given else TimeWindow(mode="latest")
    else:
        time = _parse_time(text, intent)
    if overrides.get("year"):
        year = int(overrides["year"])
        if intent == "event_history":
            yesterday = (_today() - timedelta(days=1)).strftime("%Y%m%d")
            time = TimeWindow(mode="range", start=f"{year}0101", end=min(f"{year}1231", yesterday), label=str(year))
        else:
            time = TimeWindow(mode="year", year=year)
    if overrides.get("date"):
        day = str(overrides["date"]).replace("-", "")
        if intent == "event_history":
            time = TimeWindow(mode="range", start=day, end=day, label=f"{day[:4]}-{day[4:6]}-{day[6:]}")
        else:
            time = TimeWindow(mode="date", date=day, year=int(day[:4]))

    limit_match = re.search(r"\btop (\d{1,2})\b", text, re.I)
    plan = QueryPlan(
        question=reformulation.original,
        intent=intent,
        entities=entities,
        time=time,
        sector=overrides.get("sector") or _sector(text),
        severity=float(overrides["severity"]) if "severity" in overrides else (_severity(text) if intent == "shock" else None),
        metric="imports" if re.search(r"\bimports?\b", text, re.I) else "exports",
        limit=max(1, min(20, int(limit_match.group(1)))) if limit_match else (10 if intent == "ranking" else 5),
    )
    if intent == "event_history":
        plan.direction = _direction(text)
        if not range_given and not overrides.get("year") and not overrides.get("date"):
            plan.notes.append("No time range given; searching the last year of daily GDELT history.")
        if len(entities) >= 2:
            plan.notes.append(f"The history is indexed per country, so this searches {entities[0].name}'s days with all counterparts, not only those with {entities[1].name}.")
    if intent == "vote_alignment":
        plan.topic, plan.topic_terms = _vote_topic(text, entities)
        if not plan.topic:
            plan.notes.append("No topic recognised; comparing every UN General Assembly vote.")
    if intent == "ranking":
        plan.trade_query = _trade_ranking_query(text)
    elif intent == "blocs":
        plan.trade_query = "blocs"

    candidates = dict(_INTENT_AGENTS[intent])
    plan.focus_agents = [agent for agent, pattern in _DOMAIN.items() if pattern.search(text)]

    requested = overrides.get("agents")
    if requested:
        chosen = [agent for agent in ALL_AGENTS if agent in requested]
        for agent in chosen:
            candidates.setdefault(agent, "requested explicitly")
        plan.notes.append("agent selection overridden by the request")
    else:
        chosen = [agent for agent in ALL_AGENTS if agent in candidates]

    if intent == "ranking" and not requested:
        # A global ranking question is about one domain; ask the agents it names,
        # or all three annual agents when it names none.
        named = [agent for agent in chosen if agent in plan.focus_agents]
        if named:
            chosen = named

    if time.mode == "year" and "event_summarization" in chosen and intent != "bilateral":
        # GDELT daily data answers "what is happening", not "what was true in 2012".
        chosen.remove("event_summarization")
        plan.notes.append(f"Events agent skipped: daily GDELT activity does not describe the year {time.year}.")

    plan.agents = chosen
    plan.agent_reasons = {agent: candidates[agent] for agent in chosen}
    plan.agent_relevance, plan.agent_order, plan.lead_reason = _rank_agents(intent, text, time, chosen)

    if not entities and intent not in ("ranking", "blocs"):
        plan.notes.append("No country recognised in the question; answering with a global overview.")
    if len(entities) > 2 and intent == "bilateral":
        plan.notes.append(f"{len(entities)} countries mentioned; comparing the first two.")
    if time.horizon_year:
        plan.notes.append(f"Forecast horizon read as {time.horizon_year}.")

    if reformulation.changed:
        plan.read_as = text
    plan.rewrites = [rewrite.to_dict() for rewrite in reformulation.rewrites]
    plan.notes = [rewrite.note() for rewrite in reformulation.rewrites] + plan.notes
    plan.understanding = _understanding(plan, reformulation, text, overrides)
    return plan
