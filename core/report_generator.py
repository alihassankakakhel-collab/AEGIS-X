from __future__ import annotations

import json

from core.gemini_client import GeminiClient


def generate_situation_report(
    client: GeminiClient,
    incidents: list[dict],
) -> str:
    if not incidents:
        return "There are no submitted incidents to summarize."

    selected_incidents = incidents[:50]
    facts = [
        {
            "id": item["id"],
            "category": item.get("category") or "Unknown",
            "description": item["description"],
            "location": item.get("location"),
            "reported_at": item.get("reported_at"),
            "priority": item["severity"],
            "status": item["status"],
            "uncertainty": item.get("uncertainty_json") or "[]",
        }
        for item in selected_incidents
    ]
    scope = (
        f"the latest {len(selected_incidents)} of {len(incidents)} records"
        if len(incidents) > len(selected_incidents)
        else f"all {len(facts)} submitted records"
    )
    prompt = (
        "Write a concise draft situation report using only the supplied incident "
        "records. Separate reported facts from unknowns, do not add outside facts, "
        "do not claim that reports are verified, and do not recommend or initiate "
        "dispatch. State that an authorized operator must review this draft. "
        f"The supplied records represent {scope}.\n\n"
        f"Incident records:\n{json.dumps(facts, ensure_ascii=True)}"
    )
    return client.generate(prompt)
