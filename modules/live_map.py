from __future__ import annotations

from html import escape

import folium
import streamlit as st
from streamlit_folium import st_folium


PESHAWAR_CITY_CENTER = (34.0123846, 71.5787458)


def render_live_map(incidents: list[dict]) -> None:
    st.caption(
        "Only explicitly stored coordinates are mapped. Reported locations are "
        "unverified; fictional demo markers are road-reference points, not exact "
        "event locations. Map and reference-point data: © OpenStreetMap contributors."
    )
    st.markdown(
        """
        <div style="display:flex;gap:16px;margin:0 0 8px;color:#cbd5e1;font-size:12px">
          <span><b style="color:#fb923c">●</b> SIMULATED demo incident</span>
          <span><b style="color:#f87171">●</b> Submitted report coordinate</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    city_map = folium.Map(
        location=PESHAWAR_CITY_CENTER,
        zoom_start=12,
        control_scale=True,
    )
    mapped_count = 0

    for incident in incidents:
        latitude = incident.get("latitude")
        longitude = incident.get("longitude")
        if latitude is None or longitude is None:
            continue

        simulated = bool(incident.get("demo_key"))
        simulation_label = "SIMULATION ONLY — " if simulated else ""
        folium.Marker(
            location=(latitude, longitude),
            tooltip=escape(
                f"{simulation_label}Incident #{incident['id']}: "
                f"{incident.get('category') or 'Unknown'}"
            ),
            popup=folium.Popup(
                f"{escape(simulation_label)}{escape(incident['severity'])} priority — "
                f"{escape(incident['status'])}<br>{escape(incident['description'])}",
                max_width=350,
            ),
            icon=folium.Icon(
                color="orange" if simulated else "red",
                icon="info-sign" if simulated else "warning-sign",
            ),
        ).add_to(city_map)
        mapped_count += 1

    if not mapped_count:
        st.info(
            "No reports have explicit coordinates. The map is centered on Peshawar "
            "for orientation only; no incident locations are inferred."
        )
    st_folium(city_map, use_container_width=True, height=560, key="incident-map")
