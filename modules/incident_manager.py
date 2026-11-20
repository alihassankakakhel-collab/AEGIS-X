from __future__ import annotations

from pathlib import Path

import streamlit as st

from database.connection import fetch_incidents
from database.operations import INCIDENT_STATUSES, update_incident_status


def render_incident_manager(
    database_path: Path,
    actor: str = "Operator",
) -> None:
    st.subheader("Incident management")
    incidents = fetch_incidents(database_path)
    if not incidents:
        st.info("No incidents have been submitted.")
        return

    st.dataframe(
        [
            {
                "ID": item["id"],
                "Category": item.get("category") or "Unclassified",
                "Priority suggestion": item["severity"],
                "Status": item["status"],
                "Reported location": item.get("location") or "Not reported",
                "Description": item["description"],
                "Verification": "Operator verified"
                if item["status"] == "Verified"
                else "Unverified",
            }
            for item in incidents
        ],
        width="stretch",
        hide_index=True,
    )

    for incident in incidents:
        with st.expander(
            f"Incident #{incident['id']} — {incident['category'] or 'Unclassified'}"
        ):
            st.write(incident["description"])
            st.caption(
                incident.get("priority_explanation")
                or "Legacy record; no explainable priority details are available."
            )
            factors = incident.get("priority_factors_json")
            if factors and factors != "[]":
                st.write("Priority factors:", factors)
            missing = incident.get("missing_information_json")
            if missing and missing != "[]":
                st.write("Missing information:", missing)

            current_status = incident["status"]
            default_index = (
                INCIDENT_STATUSES.index(current_status)
                if current_status in INCIDENT_STATUSES
                else 0
            )
            with st.form(f"incident-status-{incident['id']}"):
                new_status = st.selectbox(
                    "Operator-controlled status",
                    INCIDENT_STATUSES,
                    index=default_index,
                    key=f"status-select-{incident['id']}",
                )
                submitted = st.form_submit_button("Save status")
            if submitted:
                try:
                    update_incident_status(
                        database_path,
                        incident["id"],
                        new_status,
                        actor=actor,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Status updated and logged.")
                    st.rerun()
