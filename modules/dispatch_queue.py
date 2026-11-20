from __future__ import annotations

from pathlib import Path
import sqlite3

import streamlit as st

from database.operations import (
    DISPATCH_REQUEST_TYPES,
    create_dispatch_request,
    fetch_dispatch_requests,
    review_dispatch_request,
)


def render_dispatch_queue(
    database_path: Path,
    incidents: list[dict],
    actor: str,
) -> None:
    st.subheader("Human-reviewed dispatch request queue")
    st.warning(
        "Local queue only: requests are saved in SQLite and are never transmitted. "
        "There is no emergency-service, messaging-provider, or automatic dispatch "
        "integration. Approval records local operator review only."
    )

    open_incidents = [
        item for item in incidents if item["status"] not in {"Resolved", "Closed"}
    ]
    if not open_incidents:
        st.info("Submit or load an open incident before drafting a request.")
    else:
        incident_labels = {
            f"#{item['id']} — {item.get('category') or 'Incident'}: "
            f"{item['description'][:80]}": item["id"]
            for item in open_incidents
        }
        with st.form("create-local-dispatch-request"):
            selected_incident = st.selectbox(
                "Related open incident",
                list(incident_labels),
            )
            request_type = st.selectbox(
                "Request type",
                DISPATCH_REQUEST_TYPES,
            )
            summary = st.text_area(
                "Request summary",
                placeholder="Draft the request for operator review. Do not include credentials.",
                max_chars=2000,
            )
            submitted = st.form_submit_button("Save request for approval")
        if submitted:
            try:
                create_dispatch_request(
                    database_path,
                    incident_labels[selected_incident],
                    request_type,
                    summary,
                    actor=actor,
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not save request: {error}")
            else:
                st.success("Request saved locally as pending approval; nothing was sent.")
                st.rerun()

    requests = fetch_dispatch_requests(database_path)
    if not requests:
        st.info("No local dispatch requests have been drafted.")
        return

    st.dataframe(
        [
            {
                "ID": item["id"],
                "Incident": item["incident_id"],
                "Type": item["request_type"],
                "Status": item["status"],
                "Created by": item["created_by"],
                "Approved by": item["approved_by"] or "—",
                "Created": item["created_at"],
            }
            for item in requests
        ],
        width="stretch",
        hide_index=True,
    )
    for request in requests:
        with st.expander(
            f"Request #{request['id']} — {request['request_type']} "
            f"({request['status']})"
        ):
            st.write(request["summary"])
            st.caption(
                f"Incident #{request['incident_id']}: "
                f"{request['incident_description']}"
            )
            if request["status"] == "Pending approval":
                approve_column, cancel_column = st.columns(2)
                if approve_column.button(
                    "Approve locally (not sent)",
                    key=f"approve-local-request-{request['id']}",
                ):
                    try:
                        review_dispatch_request(
                            database_path,
                            request["id"],
                            "approve",
                            actor=actor,
                        )
                    except (ValueError, sqlite3.Error) as error:
                        st.error(f"Could not approve request: {error}")
                    else:
                        st.success("Approved locally. No external request was sent.")
                        st.rerun()
                if cancel_column.button(
                    "Cancel request",
                    key=f"cancel-local-request-{request['id']}",
                ):
                    try:
                        review_dispatch_request(
                            database_path,
                            request["id"],
                            "cancel",
                            actor=actor,
                        )
                    except (ValueError, sqlite3.Error) as error:
                        st.error(f"Could not cancel request: {error}")
                    else:
                        st.success("Request cancelled.")
                        st.rerun()
            elif request["status"] == "Approved (not sent)":
                st.info(
                    f"Locally approved by {request['approved_by']}; not transmitted."
                )
