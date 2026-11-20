from __future__ import annotations

from pathlib import Path
import sqlite3

import streamlit as st

from database.operations import (
    add_resource,
    add_response_team,
    assign_response_team,
    fetch_resources,
    fetch_resource_assignments,
    fetch_response_teams,
    return_response_assignment,
)


def render_resource_coordination(
    database_path: Path,
    incidents: list[dict],
    actor: str,
) -> None:
    st.subheader("Resource and team coordination")
    st.warning(
        "Assignments are a local simulation only. This feature does not contact, "
        "dispatch, or track real response teams."
    )
    st.markdown("#### Incident status")
    if incidents:
        st.dataframe(
            [
                {
                    "ID": item["id"],
                    "Category": item.get("category") or "Unclassified",
                    "Priority suggestion": item["severity"],
                    "Status": item["status"],
                    "Location": item.get("location") or "Not reported",
                }
                for item in incidents[:12]
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption("Use the Incidents view to verify reports or change incident status.")
    else:
        st.info("No incidents are available for response coordination yet.")

    teams_column, resources_column = st.columns(2)
    with teams_column:
        st.markdown("#### Register a response team")
        with st.form("add-response-team"):
            team_name = st.text_input("Team name")
            base_location = st.text_input("Declared base location")
            team_latitude = st.number_input(
                "Base latitude", min_value=-90.0, max_value=90.0,
                value=34.0124, format="%.6f",
            )
            team_longitude = st.number_input(
                "Base longitude", min_value=-180.0, max_value=180.0,
                value=71.5787, format="%.6f",
            )
            team_notes = st.text_input("Notes (optional)")
            team_submitted = st.form_submit_button("Add team")
        if team_submitted:
            try:
                add_response_team(
                    database_path,
                    team_name,
                    base_location,
                    team_latitude,
                    team_longitude,
                    notes=team_notes,
                    actor=actor,
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not add response team: {error}")
            else:
                st.success("Response team added.")
                st.rerun()

    with resources_column:
        st.markdown("#### Add resource inventory")
        with st.form("add-resource-inventory"):
            resource_name = st.text_input("Inventory item name")
            resource_type = st.text_input("Resource type", placeholder="Medical kits")
            resource_quantity = st.number_input(
                "Total available units", min_value=1, max_value=100_000,
                value=1, step=1,
            )
            resource_submitted = st.form_submit_button("Add inventory")
        if resource_submitted:
            try:
                add_resource(
                    database_path,
                    resource_name,
                    resource_type,
                    resource_quantity,
                    actor=actor,
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not add resource inventory: {error}")
            else:
                st.success("Resource inventory added.")
                st.rerun()

    teams = fetch_response_teams(database_path)
    resources = fetch_resources(database_path)
    st.markdown("#### Current teams")
    st.dataframe(
        [
            {
                "Team": team["name"],
                "Declared base": team["base_location"],
                "Status": team["status"],
                "Base coordinates": f"{team['latitude']:.5f}, {team['longitude']:.5f}",
                "Notes": team["notes"],
            }
            for team in teams
        ],
        width="stretch",
        hide_index=True,
    )
    st.markdown("#### Current inventory")
    st.dataframe(
        [
            {
                "Item": item["name"],
                "Type": item["resource_type"],
                "Available": item["quantity_available"],
                "Total": item["quantity_total"],
            }
            for item in resources
        ],
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### Create simulated assignment")
    if not incidents:
        st.info("Submit or load an incident before assigning a team.")
    elif not any(team["status"] == "Available" for team in teams):
        st.info("No response teams are currently available.")
    else:
        resources_by_label = {
            f"{item['name']} ({item['quantity_available']} available, "
            f"inventory #{item['id']})": item
            for item in resources
        }
        resource_labels = ["No additional resource", *resources_by_label]
        selected_resource_label = st.selectbox(
            "Optional inventory item",
            resource_labels,
            key="assignment-resource",
        )
        selected_resource = resources_by_label.get(selected_resource_label)
        available_quantity = (
            selected_resource["quantity_available"] if selected_resource else 0
        )
        assignable_incidents = [
            item for item in incidents if item["status"] not in {"Resolved", "Closed"}
        ]
        if not assignable_incidents:
            st.info("There are no open incidents to assign.")
        else:
            incident_labels = {
                f"#{item['id']} — {item.get('category') or 'Incident'}: "
                f"{item['description'][:80]}": item["id"]
                for item in assignable_incidents
            }
            available_teams = [team for team in teams if team["status"] == "Available"]
            with st.form("assign-response-team"):
                selected_incident = st.selectbox(
                    "Open incident", list(incident_labels)
                )
                team_labels = {
                    f"{team['name']} — {team['base_location']}": team["id"]
                    for team in available_teams
                }
                selected_team = st.selectbox("Available team", list(team_labels))
                quantity = st.number_input(
                    "Resource units to reserve",
                    min_value=0,
                    max_value=available_quantity,
                    value=1 if available_quantity else 0,
                    step=1,
                    disabled=selected_resource is None or available_quantity == 0,
                )
                assign_submitted = st.form_submit_button(
                    "Create simulated assignment",
                    disabled=selected_resource is not None and available_quantity == 0,
                )
            if assign_submitted:
                try:
                    assign_response_team(
                        database_path,
                        incident_labels[selected_incident],
                        team_labels[selected_team],
                        resource_id=(
                            selected_resource["id"] if selected_resource else None
                        ),
                        resource_quantity=quantity if selected_resource else 0,
                        actor=actor,
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"Could not create assignment: {error}")
                else:
                    st.success("Simulated assignment created and logged.")
                    st.rerun()

    st.markdown("#### Assignment history")
    assignments = fetch_resource_assignments(database_path)
    if not assignments:
        st.info("No simulated assignments have been created.")
    else:
        st.dataframe(
            [
                {
                    "ID": item["id"],
                    "Incident": item["incident_id"],
                    "Team": item["team_name"],
                    "Resource": item["resource_name"] or "None",
                    "Units": item["resource_quantity"],
                    "Status": item["status"],
                    "Created": item["created_at"],
                }
                for item in assignments
            ],
            width="stretch",
            hide_index=True,
        )
        for assignment in assignments:
            if assignment["status"] == "Active" and st.button(
                f"Return team and resources for assignment #{assignment['id']}",
                key=f"return-assignment-{assignment['id']}",
            ):
                try:
                    return_response_assignment(
                        database_path,
                        assignment["id"],
                        actor=actor,
                    )
                except (ValueError, sqlite3.Error) as error:
                    st.error(f"Could not return assignment: {error}")
                else:
                    st.success("Team and reserved resources returned.")
                    st.rerun()
