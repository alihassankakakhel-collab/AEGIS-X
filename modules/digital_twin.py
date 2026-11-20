from __future__ import annotations

from html import escape
import math
from pathlib import Path
import sqlite3

import folium
import streamlit as st
from streamlit_folium import st_folium

from database.operations import (
    add_simulation_scenario,
    fetch_simulation_scenarios,
    set_simulation_scenario_active,
)


CITY_CENTER = (34.0123846, 71.5787458)


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_km = 6371.0088
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = math.radians(latitude_b - latitude_a)
    delta_lon = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_km * 2 * math.asin(math.sqrt(min(1.0, value)))


def _render_scenario_map(
    incidents: list[dict],
    teams: list[dict],
    scenarios: list[dict],
    assignments: list[dict],
    *,
    show_scenarios: bool,
    key: str,
) -> None:
    city_map = folium.Map(location=CITY_CENTER, zoom_start=12, control_scale=True)
    for incident in incidents:
        latitude, longitude = incident.get("latitude"), incident.get("longitude")
        if latitude is None or longitude is None:
            continue
        folium.Marker(
            location=(latitude, longitude),
            tooltip=escape(f"Incident #{incident['id']}"),
            popup=folium.Popup(
                f"{escape(incident.get('severity', 'Unknown'))} priority — "
                f"{escape(incident['description'])}",
                max_width=350,
            ),
            icon=folium.Icon(color="red", icon="warning-sign"),
        ).add_to(city_map)

    for team in teams:
        folium.Marker(
            location=(team["latitude"], team["longitude"]),
            tooltip=escape(f"Declared base: {team['name']}"),
            popup=folium.Popup(
                f"Operator-entered base: {escape(team['base_location'])}. "
                "This is not live GPS tracking.",
                max_width=300,
            ),
            icon=folium.Icon(color="blue", icon="home"),
        ).add_to(city_map)

    active_scenarios = [scenario for scenario in scenarios if scenario["active"]]
    if show_scenarios:
        for scenario in active_scenarios:
            color = "orange" if scenario["scenario_type"] == "Road closure" else "blue"
            folium.Circle(
                location=(scenario["latitude"], scenario["longitude"]),
                radius=scenario["radius_m"],
                color=color,
                fill=True,
                fill_opacity=0.2,
                tooltip=escape(
                    f"HYPOTHETICAL {scenario['scenario_type']}: {scenario['name']} "
                    f"({scenario['radius_m']} m radius)"
                ),
            ).add_to(city_map)

        for assignment in assignments:
            if (
                assignment["status"] != "Active"
                or assignment["incident_latitude"] is None
                or assignment["incident_longitude"] is None
            ):
                continue
            folium.PolyLine(
                [
                    (assignment["team_latitude"], assignment["team_longitude"]),
                    (assignment["incident_latitude"], assignment["incident_longitude"]),
                ],
                color="purple",
                weight=2,
                dash_array="6",
                tooltip="Hypothetical straight-line comparison — not a road route",
            ).add_to(city_map)

    st_folium(city_map, use_container_width=True, height=500, key=key)


def render_digital_twin(
    database_path: Path,
    incidents: list[dict],
    teams: list[dict],
    assignments: list[dict],
    actor: str,
) -> None:
    st.subheader("Digital-twin scenario comparison")
    st.warning(
        "Hypothetical geometry only: circles are operator-entered zones, not "
        "predictions. No flood model, road graph, geocoding, or live GPS is used. "
        "Dashed team lines are straight-line comparisons, not travel routes."
    )

    with st.form("create-simulation-scenario"):
        scenario_name = st.text_input("Scenario name")
        scenario_type = st.selectbox(
            "Hypothetical condition", ["Road closure", "Flood zone"]
        )
        latitude = st.number_input(
            "Scenario latitude", min_value=-90.0, max_value=90.0,
            value=CITY_CENTER[0], format="%.6f",
        )
        longitude = st.number_input(
            "Scenario longitude", min_value=-180.0, max_value=180.0,
            value=CITY_CENTER[1], format="%.6f",
        )
        radius_m = st.number_input(
            "Hypothetical radius (metres)", min_value=50, max_value=50_000,
            value=500, step=50,
        )
        notes = st.text_input("Scenario notes (optional)")
        submitted = st.form_submit_button("Save inactive scenario")
    if submitted:
        try:
            add_simulation_scenario(
                database_path,
                scenario_name,
                scenario_type,
                latitude,
                longitude,
                radius_m,
                notes=notes,
                actor=actor,
            )
        except (ValueError, sqlite3.Error) as error:
            st.error(f"Could not save scenario: {error}")
        else:
            st.success("Scenario saved inactive. Activate it to compare the overlay.")
            st.rerun()

    scenarios = fetch_simulation_scenarios(database_path)
    for scenario in scenarios:
        active = bool(scenario["active"])
        st.write(
            f"**{scenario['name']}** — {scenario['scenario_type']}, "
            f"{scenario['radius_m']} m radius — "
            f"{'Active overlay' if active else 'Inactive'}"
        )
        if st.button(
            "Deactivate scenario" if active else "Activate scenario",
            key=f"scenario-toggle-{scenario['id']}",
        ):
            try:
                set_simulation_scenario_active(
                    database_path,
                    scenario["id"],
                    not active,
                    actor=actor,
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not change scenario state: {error}")
            else:
                st.rerun()

    active_scenarios = [scenario for scenario in scenarios if scenario["active"]]
    potentially_affected: dict[int, set[str]] = {}
    for incident in incidents:
        incident_lat = incident.get("latitude")
        incident_lon = incident.get("longitude")
        if incident_lat is None or incident_lon is None:
            continue
        for scenario in active_scenarios:
            if haversine_km(
                incident_lat,
                incident_lon,
                scenario["latitude"],
                scenario["longitude"],
            ) * 1000 <= scenario["radius_m"]:
                potentially_affected.setdefault(incident["id"], set()).add(
                    scenario["name"]
                )

    metrics = st.columns(3)
    metrics[0].metric("Hypothetical active zones", len(active_scenarios))
    metrics[1].metric(
        "Incidents with coordinates",
        sum(
            item.get("latitude") is not None
            and item.get("longitude") is not None
            for item in incidents
        ),
    )
    metrics[2].metric("Incidents within zone radii", len(potentially_affected))
    if potentially_affected:
        st.dataframe(
            [
                {
                    "Incident ID": incident_id,
                    "Hypothetical nearby zones": ", ".join(sorted(zone_names)),
                    "Interpretation": "Geometric overlap only; not a forecast",
                }
                for incident_id, zone_names in sorted(potentially_affected.items())
            ],
            width="stretch",
            hide_index=True,
        )

    before_column, after_column = st.columns(2)
    with before_column:
        st.markdown("#### Before: baseline")
        st.caption("Stored incident coordinates and declared team bases only.")
        _render_scenario_map(
            incidents,
            teams,
            scenarios,
            assignments,
            show_scenarios=False,
            key="digital-twin-before",
        )
    with after_column:
        st.markdown("#### After: hypothetical overlays")
        st.caption("Active scenario circles and active simulated assignments.")
        _render_scenario_map(
            incidents,
            teams,
            scenarios,
            assignments,
            show_scenarios=True,
            key="digital-twin-after",
        )
