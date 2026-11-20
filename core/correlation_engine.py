from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass(frozen=True)
class ReportSummary:
    report_id: str
    category: str
    description: str
    location: str | None


@dataclass(frozen=True)
class DuplicateSuggestion:
    report_id: str
    score: float
    reasons: tuple[str, ...]


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def suggest_duplicates(
    candidate: ReportSummary,
    existing_reports: list[ReportSummary],
    *,
    threshold: float = 0.55,
) -> list[DuplicateSuggestion]:
    suggestions: list[DuplicateSuggestion] = []
    candidate_description = _normalize(candidate.description)
    candidate_location = _normalize(candidate.location)

    for existing in existing_reports:
        if existing.report_id == candidate.report_id:
            continue

        description_similarity = SequenceMatcher(
            None,
            candidate_description,
            _normalize(existing.description),
        ).ratio()
        same_category = _normalize(candidate.category) == _normalize(existing.category)
        same_location = bool(candidate_location) and (
            candidate_location == _normalize(existing.location)
        )
        score = 0.7 * description_similarity
        reasons = [f"Description similarity: {description_similarity:.0%}"]
        if same_category:
            score += 0.2
            reasons.append("Same incident category")
        if same_location:
            score += 0.1
            reasons.append("Same reported location")

        if score >= threshold:
            suggestions.append(
                DuplicateSuggestion(
                    report_id=existing.report_id,
                    score=min(score, 1.0),
                    reasons=tuple(reasons),
                )
            )

    return sorted(suggestions, key=lambda suggestion: suggestion.score, reverse=True)
