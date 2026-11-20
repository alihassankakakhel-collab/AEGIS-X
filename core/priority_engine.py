from __future__ import annotations

from dataclasses import dataclass

from core.incident_classifier import IncidentExtraction


@dataclass(frozen=True)
class PriorityAssessment:
    suggested_priority: str
    score: int
    factors: tuple[str, ...]
    missing_information: tuple[str, ...]
    explanation: str


def assess_priority(incident: IncidentExtraction) -> PriorityAssessment:
    score = 0
    factors: list[str] = []

    if incident.fatalities_count is not None and incident.fatalities_count > 0:
        score += 100
        factors.append(f"{incident.fatalities_count} reported fatality/fatalities (+100)")
    if incident.injuries_count is not None and incident.injuries_count > 0:
        score += 60
        factors.append(f"{incident.injuries_count} reported injury/injuries (+60)")
    if incident.affected_count is not None and incident.affected_count >= 10:
        score += 20
        factors.append("At least 10 people reported affected (+20)")
    elif incident.affected_count is not None and incident.affected_count > 0:
        score += 10
        factors.append("People reported affected (+10)")
    if incident.potential_hazards:
        score += 25
        factors.append("Potential hazard reported (+25)")
    if incident.infrastructure_damage:
        score += 15
        factors.append("Infrastructure damage reported (+15)")
    if incident.category in {"Fire", "Flooding", "Building collapse"}:
        score += 15
        factors.append(f"{incident.category} category (+15)")

    if score >= 100:
        priority = "Critical"
    elif score >= 60:
        priority = "High"
    elif score >= 25:
        priority = "Medium"
    else:
        priority = "Low"

    missing: list[str] = []
    if not incident.location:
        missing.append("location")
    if not incident.reported_at:
        missing.append("incident date/time")
    if (
        incident.affected_count is None
        and incident.injuries_count is None
        and incident.fatalities_count is None
    ):
        missing.append("impact/casualty details")

    explanation = (
        f"Suggested {priority} priority from a transparent local rule score of {score}. "
        "This is an unvalidated decision-support indicator, not an emergency severity "
        "standard. An authorized operator must verify the report and make all response decisions."
    )
    return PriorityAssessment(
        suggested_priority=priority,
        score=score,
        factors=tuple(factors),
        missing_information=tuple(missing),
        explanation=explanation,
    )
