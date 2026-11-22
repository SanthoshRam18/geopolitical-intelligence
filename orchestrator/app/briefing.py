"""Compose the fused findings into a readable, cited briefing.

Every sentence in the briefing comes from a finding, and every finding cites the
agent(s) it rests on and its confidence. Nothing here generates new claims; it
orders and phrases what fusion produced, so the narrative can always be traced
back to an agent response.
"""

from __future__ import annotations

from . import countries
from .agents.base import AgentResult
from .contract import AGENT_LABELS
from .intent import QueryPlan

# Claims shown per agent section. Events makes up to ten for a country
# (day, partners, themes, domestic split, three headlines, three baselines).
SECTION_LIMIT = 10

# Below this understanding confidence the briefing warns that the question may
# have been misread (intent.parse sets it, e.g. 0.4 when no country was found).
LOW_UNDERSTANDING = 0.5

# The claims that lead an agent's summary line and its section for a kind of
# question, in this order, when confidence alone would pick others. "What
# happened" is answered by the day's activity, not by the (more confident)
# 1990-2024 baseline beside it; a bilateral question by the pair, not by the
# first country's whole day, which follows as context.
LEAD_FACETS = {
    "events": {"event_summarization": ("event_activity",)},
    "bilateral": {"event_summarization": ("bilateral_events", "event_headline", "bilateral_headlines", "relationship_baseline")},
    "event_history": {"event_summarization": ("event_extreme_day", "event_extreme_events", "event_history_baseline")},
    "vote_alignment": {"policy_stance": ("vote_alignment", "vote_pair", "vote_opposition", "topic_stance")},
}


def _lead_order(finding: dict, facets: tuple[str, ...]) -> int:
    return next((i for i, facet in enumerate(facets) if facet in finding["facets"]), len(facets))


def _cite(finding: dict) -> str:
    labels = "+".join(finding.get("agent_labels") or [])
    return f"[{labels}, conf {finding['confidence']:.2f}]"


def _headline(plan: QueryPlan, findings: list[dict], results: dict[str, AgentResult]) -> str:
    names = [countries.name_of(code) for code in plan.iso3s[:2]]
    lead = next((f for f in findings if f["kind"] in ("divergence", "corroboration")), None)
    if plan.intent == "vote_alignment" and names:
        about = f" on {plan.topic}" if plan.topic else ""
        if len(plan.iso3s) > 1 and not (plan.topic and plan.topic == names[1].lower()):
            return f"{names[0]} and {names[1]}: how they vote at the UN{about}"
        return f"{names[0]}: who votes with it at the UN{about}"
    if plan.intent == "event_history" and names:
        superlative = "most cooperative" if plan.direction == "cooperation" else "most conflictual"
        label = plan.time.label or "the range searched"
        return f"{names[0]}: {superlative} days {'' if label.startswith('since') else 'in '}{label}"
    if plan.intent == "events" and names:
        day = (results.get("event_summarization") and results["event_summarization"].context.get("date")) or ""
        when = f" on {day[:4]}-{day[4:6]}-{day[6:]}" if day else ""
        return f"{names[0]}: what the news shows{when}"
    if plan.intent == "bilateral" and len(names) == 2:
        subject = f"{names[0]} and {names[1]}"
    elif plan.intent == "shock" and names:
        pct = int(round((plan.severity or 0.5) * 100))
        sector = "" if plan.sector == "all" else f" {plan.sector}"
        return f"What a {pct}% cut in {names[0]}'s{sector} exports would do"
    elif plan.intent == "ranking" or not names:
        return "Global overview"
    else:
        subject = names[0]
    if lead:
        title = lead["title"].split(": ", 1)[-1]
        return f"{subject}: {title[0].lower() + title[1:]}"
    return f"{subject}: fused briefing"


def _coverage(plan: QueryPlan, results: dict[str, AgentResult], alignment: dict) -> str:
    answered = [a for a, r in results.items() if r.status in ("ok", "partial") and r.insights]
    parts = []
    for agent in plan.agents:
        result = results.get(agent)
        label = AGENT_LABELS.get(agent, agent)
        if not result:
            continue
        if result.status in ("ok", "partial") and result.insights:
            served = alignment.get("served", {}).get(agent)
            parts.append(f"{label} ({served})" if served else label)
        else:
            parts.append(f"{label}: {result.status}")
    return f"{len(answered)} of {len(plan.agents)} agents answered - " + "; ".join(parts) + "."


def compose(plan: QueryPlan, fused: dict, results: dict[str, AgentResult], alignment: dict) -> dict:
    findings = fused["findings"]
    divergences = [f for f in findings if f["kind"] == "divergence"]
    agreements = [f for f in findings if f["kind"] == "corroboration"]
    cross = [f for f in findings if f["kind"] == "cross_agent"]
    singles = [f for f in findings if f["kind"] == "insight"]

    summary: list[str] = []
    prefix = {"divergence": "Key tension: ", "corroboration": "Agents agree: ", "cross_agent": ""}
    for finding in [f for f in findings if f["kind"] != "insight"][:2]:
        summary.append(f"{prefix[finding['kind']]}{finding['claim']} {_cite(finding)}")
    # Then the strongest claim from each agent in turn - agents the question is
    # about first - so one agent with high confidences cannot crowd out the rest.
    best_per_agent: dict[str, dict] = {}
    for finding in singles:
        best_per_agent.setdefault(finding["agents"][0], finding)
    for agent, facets in LEAD_FACETS.get(plan.intent, {}).items():
        leads = sorted((f for f in singles if f["agents"][0] == agent and _lead_order(f, facets) < len(facets)), key=lambda f: _lead_order(f, facets))
        if leads:
            best_per_agent[agent] = leads[0]
    # Most relevant agent first (intent.parse ranks them); plans built without a ranking fall back to focus order.
    ordered = list(plan.agent_order) or ([a for a in plan.agents if a in plan.focus_agents] + [a for a in plan.agents if a not in plan.focus_agents])
    for agent in ordered:
        if len(summary) >= 5:
            break
        if agent in best_per_agent:
            summary.append(f"{best_per_agent[agent]['claim']} {_cite(best_per_agent[agent])}")
    if plan.intent == "event_history":
        # The question asks for days: the two top-ranked days and the single most
        # severe events answer it, then each other agent's strongest claim as
        # context, in focus order. Findings that tie on rank keep the adapter's
        # order, so the days stay in rank order.
        facets = LEAD_FACETS["event_history"]["event_summarization"]
        picks = sorted((f for f in singles if f["agents"][0] == "event_summarization" and _lead_order(f, facets) < len(facets)), key=lambda f: _lead_order(f, facets))
        lead = [f for f in picks if "event_extreme_day" in f["facets"]][:2] + [f for f in picks if "event_extreme_events" in f["facets"]][:1]
        context_order = ordered
        context = [best_per_agent[a] for a in context_order if a != "event_summarization" and a in best_per_agent]
        summary = [f"{f['claim']} {_cite(f)}" for f in (lead or picks[:1]) + context][:6]
    if plan.intent == "vote_alignment":
        # The ranking answers the question; then each other agent's strongest claim.
        facets = LEAD_FACETS["vote_alignment"]["policy_stance"]
        answer = sorted((f for f in singles if f["agents"][0] == "policy_stance" and _lead_order(f, facets) < len(facets)), key=lambda f: _lead_order(f, facets))[:3]
        context = [best_per_agent[a] for a in ordered if a != "policy_stance" and a in best_per_agent]
        if answer:
            summary = [f"{f['claim']} {_cite(f)}" for f in answer + context][:6]
    if not summary:
        summary.append("No agent returned usable insights for this question.")

    sections = []
    if divergences:
        sections.append({"title": "Where the agents disagree", "kind": "divergence", "findings": divergences})
    if agreements:
        sections.append({"title": "Where independent agents agree", "kind": "corroboration", "findings": agreements})
    if cross:
        sections.append({"title": "Cross-agent analysis", "kind": "cross_agent", "findings": cross})
    by_agent: dict[str, list[dict]] = {}
    for finding in singles:
        by_agent.setdefault(finding["agents"][0], []).append(finding)
    for agent, facets in LEAD_FACETS.get(plan.intent, {}).items():
        # Stable sort: the lead claims in their order, then the rest by rank.
        by_agent.get(agent, []).sort(key=lambda f: _lead_order(f, facets))
    for agent in plan.agents:
        if by_agent.get(agent):
            sections.append({"title": AGENT_LABELS.get(agent, agent), "kind": "agent", "agent": agent, "findings": by_agent[agent][:SECTION_LIMIT]})

    # Page-level caveats qualify cross-agent findings; a single agent's caveat
    # stays next to its own claim.
    caveats = sorted({f["caveat"] for f in findings if f.get("caveat") and f["kind"] != "insight"})
    coverage = _coverage(plan, results, alignment)
    headline = _headline(plan, findings, results)

    understanding = {
        "question": plan.question,
        "read_as": plan.read_as,
        "confidence": plan.understanding,
        "rewrites": plan.rewrites,
        "low": plan.understanding < LOW_UNDERSTANDING,
    }

    lines = [f"## {headline}", ""]
    if plan.read_as:
        lines += [f"_Read as: {plan.read_as} (understanding {plan.understanding:.2f})_", ""]
    if understanding["low"]:
        lines += [f"_This may not be the question that was asked (understanding {plan.understanding:.2f}). Check how it was read before relying on the answer._", ""]
    lines += [f"- {sentence}" for sentence in summary]
    lines += ["", f"_{coverage}_"]
    for section in sections:
        lines += ["", f"### {section['title']}"]
        for finding in section["findings"]:
            lines.append(f"- {finding['claim']} {_cite(finding)}")
            if section["kind"] == "agent" and finding.get("caveat"):
                lines.append(f"  - _Caveat: {finding['caveat']}_")
    if caveats:
        lines += ["", "### Caveats"] + [f"- {text}" for text in caveats]
    if alignment.get("warnings"):
        lines += ["", "### Time alignment"] + [f"- {text}" for text in alignment["warnings"]]

    return {
        "headline": headline,
        "summary": summary,
        "sections": sections,
        "caveats": caveats,
        "coverage": coverage,
        "understanding": understanding,
        "markdown": "\n".join(lines),
    }
