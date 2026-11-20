from __future__ import annotations

import re
from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field, model_validator

from core.gemini_client import GeminiClient


IncidentCategory = Literal[
    "Road accident",
    "Fire",
    "Flooding",
    "Medical emergency",
    "Building collapse",
    "Infrastructure damage",
    "Road blockage",
    "Public safety threat",
    "Other emergency",
]

_COORDINATES = re.compile(
    r"(?P<latitude>-?\d{1,2}\.\d+)\s*[,/]\s*"
    r"(?P<longitude>-?\d{1,3}\.\d+)"
)
_NUMBER_WORDS = {
    "no": 0,
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUMBER = r"\d+|no|zero|one|two|three|four|five|six|seven|eight|nine|ten"
_COUNT_KEYWORDS = {
    "affected_count": r"people|persons?|victims?|affected|casualties",
    "injuries_count": r"injur(?:y|ies|ed)|wound(?:ed)?",
    "fatalities_count": r"fatalit(?:y|ies)|deaths?|killed|dead",
}
_DATE_OR_TIME = re.compile(
    r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}[-/]\d{1,2}[-/](?:\d{2}|\d{4})|"
    r"\d{1,2}:\d{2}(?:\s*[AP]M)?)\b",
    re.IGNORECASE,
)


class IncidentExtraction(BaseModel):
    category: IncidentCategory
    description: str = Field(min_length=1, max_length=2000)
    location: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    reported_at: str | None = None
    affected_count: int | None = Field(default=None, ge=0)
    injuries_count: int | None = Field(default=None, ge=0)
    fatalities_count: int | None = Field(default=None, ge=0)
    infrastructure_damage: list[str] = Field(default_factory=list)
    potential_hazards: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coordinates_must_be_a_pair(self) -> IncidentExtraction:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Latitude and longitude must both be provided or omitted.")
        return self


def analyze_emergency_report(
    client: GeminiClient,
    original_text: str,
    *,
    image_bytes: bytes | None = None,
    image_mime_type: str | None = None,
) -> IncidentExtraction:
    if not original_text.strip() and image_bytes is None:
        raise ValueError("Provide report text or an image to analyze.")
    if (image_bytes is None) != (image_mime_type is None):
        raise ValueError("Image bytes and image MIME type must be supplied together.")

    prompt = (
        "Extract only facts explicitly present in this emergency report or image. "
        "Return the requested JSON schema. Do not infer coordinates, counts, dates, "
        "injuries, deaths, or damage. Use null for unknown scalar values and empty "
        "lists for unknown lists. Keep the report unverified; do not recommend or "
        "initiate dispatch. Put ambiguity and missing evidence in uncertainty. "
        "The original report is preserved separately.\n\n"
        f"Report text:\n{original_text or '[No text supplied; inspect image evidence only.]'}"
    )
    contents: str | list[str | types.Part] = prompt
    if image_bytes is not None and image_mime_type is not None:
        contents = [
            prompt,
            types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type),
        ]

    extraction = client.generate_json(contents, IncidentExtraction)

    updates: dict[str, object] = {}
    uncertainty = list(extraction.uncertainty)
    coordinate_pairs = [
        (float(match["latitude"]), float(match["longitude"]))
        for match in _COORDINATES.finditer(original_text)
    ]
    if extraction.latitude is not None and extraction.longitude is not None:
        coordinates_match_source = any(
            abs(extraction.latitude - latitude) < 0.000001
            and abs(extraction.longitude - longitude) < 0.000001
            for latitude, longitude in coordinate_pairs
        )
        if not coordinates_match_source:
            updates.update(latitude=None, longitude=None)
            uncertainty.append(
                "Coordinates were omitted because the extracted pair was not explicitly supplied in the source text."
            )

    for field, keywords in _COUNT_KEYWORDS.items():
        number_pattern = re.compile(
            rf"\b(?P<count>{_NUMBER})\b(?:(?![.!?\n]).){{0,30}}?\b(?:{keywords})\b"
            rf"|\b(?:{keywords})\b\s*(?::|=|of)?\s*(?P<count_after>{_NUMBER})\b",
            re.IGNORECASE,
        )
        source_counts: set[int] = set()
        for match in number_pattern.finditer(original_text):
            token = match["count"] or match["count_after"]
            source_counts.add(
                int(token) if token.isdigit() else _NUMBER_WORDS[token.casefold()]
            )
        extracted_count = getattr(extraction, field)
        if extracted_count is not None and extracted_count not in source_counts:
            updates[field] = None
            uncertainty.append(
                f"{field.replace('_', ' ').capitalize()} was omitted because that count was not explicitly stated in the source text."
            )
    if not _DATE_OR_TIME.search(original_text) and extraction.reported_at is not None:
        updates["reported_at"] = None
        uncertainty.append(
            "The incident date/time was omitted because none was explicitly stated in the source text."
        )
    elif (
        extraction.reported_at is not None
        and not all(
            part in original_text
            for part in re.findall(r"\d+", extraction.reported_at)
        )
    ):
        updates["reported_at"] = None
        uncertainty.append(
            "The incident date/time was omitted because the extracted value did not match the source text."
        )
    if extraction.location and extraction.location.casefold() not in original_text.casefold():
        updates["location"] = None
        uncertainty.append(
            "The location was omitted because it was not explicitly stated in the source text."
        )
    if updates:
        updates["uncertainty"] = list(dict.fromkeys(uncertainty))
        extraction = extraction.model_copy(update=updates)
    return extraction
