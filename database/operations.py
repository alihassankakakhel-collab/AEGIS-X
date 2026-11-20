from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from core.correlation_engine import ReportSummary
from core.incident_classifier import IncidentExtraction
from core.priority_engine import PriorityAssessment, assess_priority
from database.connection import get_connection


INCIDENT_STATUSES = (
    "New",
    "Under Review",
    "Verified",
    "Assigned",
    "Responding",
    "Resolved",
    "Closed",
)


@contextmanager
def _connection(database_path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = get_connection(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_report(
    database_path: str | Path,
    original_text: str,
    *,
    source_type: str = "Text",
    attachment_filename: str | None = None,
    attachment_mime_type: str | None = None,
    attachment_data: bytes | None = None,
    attachment_text: str | None = None,
    image_filename: str | None = None,
    image_mime_type: str | None = None,
    image_data: bytes | None = None,
    demo_key: str | None = None,
    actor: str = "Operator",
) -> str:
    if source_type not in {
        "Text",
        "Voice transcript",
        "Voice recording",
        "Photo",
        "PDF",
    }:
        raise ValueError(
            "Source type must be Text, Voice transcript, Voice recording, Photo, or PDF."
        )
    if attachment_data is not None and image_data is not None:
        raise ValueError("Supply the attachment only once.")
    if attachment_data is None and image_data is not None:
        attachment_data = image_data
        attachment_mime_type = image_mime_type
        attachment_filename = image_filename
    elif any(value is not None for value in (image_filename, image_mime_type)):
        raise ValueError("Use the attachment fields for uploaded files.")
    if (attachment_data is None) != (attachment_mime_type is None):
        raise ValueError("Attachment data and MIME type must be supplied together.")
    if not original_text.strip() and attachment_data is None and not (
        attachment_text and attachment_text.strip()
    ):
        raise ValueError("A report must include text or an attachment.")

    report_id = str(uuid4())
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO reports (
                id, original_text, image_filename, image_mime_type, image_data,
                source_type, attachment_filename, attachment_mime_type,
                attachment_data, attachment_text, analysis_status, created_at, demo_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
            """,
            (
                report_id,
                original_text,
                attachment_filename if attachment_mime_type and attachment_mime_type.startswith("image/") else None,
                attachment_mime_type if attachment_mime_type and attachment_mime_type.startswith("image/") else None,
                None,
                source_type,
                attachment_filename,
                attachment_mime_type,
                attachment_data,
                attachment_text,
                _now(),
                demo_key,
            ),
        )
        connection.execute(
            """
            INSERT INTO activity_logs (report_id, action, actor, created_at)
            VALUES (?, 'Report submitted', ?, ?)
            """,
            (report_id, actor, _now()),
        )
    return report_id


def mark_report_analysis_failed(database_path: str | Path, report_id: str) -> None:
    with _connection(database_path) as connection:
        connection.execute(
            "UPDATE reports SET analysis_status = 'Failed' WHERE id = ?",
            (report_id,),
        )
        connection.execute(
            """
            INSERT INTO activity_logs (report_id, action, actor, created_at)
            VALUES (?, 'AI analysis failed; original report retained', 'System', ?)
            """,
            (report_id, _now()),
        )


def update_report_attachment_text(
    database_path: str | Path,
    report_id: str,
    attachment_text: str,
    *,
    actor: str = "System",
) -> None:
    if not attachment_text.strip():
        raise ValueError("Extracted attachment text must not be empty.")
    now = _now()
    with _connection(database_path) as connection:
        result = connection.execute(
            "UPDATE reports SET attachment_text = ? WHERE id = ?",
            (attachment_text, report_id),
        )
        if result.rowcount == 0:
            raise ValueError("Report was not found.")
        connection.execute(
            """
            INSERT INTO activity_logs (report_id, action, actor, details_json, created_at)
            VALUES (?, 'Voice audio transcribed locally', ?, ?, ?)
            """,
            (
                report_id,
                actor,
                json.dumps({"characters": len(attachment_text)}, ensure_ascii=True),
                now,
            ),
        )


def complete_report_analysis(
    database_path: str | Path,
    report_id: str,
    extraction: IncidentExtraction,
    priority: PriorityAssessment,
) -> int:
    now = _now()
    extracted = extraction.model_dump(mode="json")
    extracted_json = json.dumps(extracted, ensure_ascii=True)
    with _connection(database_path) as connection:
        report = connection.execute(
            "SELECT id FROM reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        if report is None:
            raise ValueError("The submitted report no longer exists.")

        cursor = connection.execute(
            """
            INSERT INTO incidents (timestamp, source, severity, status, description)
            VALUES (?, 'Report intake', ?, 'New', ?)
            """,
            (extraction.reported_at or now, priority.suggested_priority, extraction.description),
        )
        incident_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO incident_metadata (
                incident_id, report_id, category, location, latitude, longitude,
                reported_at, affected_count, injuries_count, fatalities_count,
                infrastructure_damage_json, potential_hazards_json,
                supporting_evidence_json, uncertainty_json, priority_score,
                priority_factors_json, missing_information_json, priority_explanation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                report_id,
                extraction.category,
                extraction.location,
                extraction.latitude,
                extraction.longitude,
                extraction.reported_at,
                extraction.affected_count,
                extraction.injuries_count,
                extraction.fatalities_count,
                json.dumps(extraction.infrastructure_damage, ensure_ascii=True),
                json.dumps(extraction.potential_hazards, ensure_ascii=True),
                json.dumps(extraction.supporting_evidence, ensure_ascii=True),
                json.dumps(extraction.uncertainty, ensure_ascii=True),
                priority.score,
                json.dumps(priority.factors, ensure_ascii=True),
                json.dumps(priority.missing_information, ensure_ascii=True),
                priority.explanation,
            ),
        )
        connection.execute(
            """
            UPDATE reports
            SET extracted_json = ?, analysis_status = 'Complete'
            WHERE id = ?
            """,
            (extracted_json, report_id),
        )
        connection.execute(
            """
            INSERT INTO activity_logs (report_id, incident_id, action, actor, created_at)
            VALUES (?, ?, 'Incident created from report', 'System', ?)
            """,
            (report_id, incident_id, now),
        )
    return incident_id


def fetch_reports(database_path: str | Path) -> list[dict]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, original_text, source_type,
                   COALESCE(attachment_filename, image_filename) AS attachment_filename,
                   COALESCE(attachment_mime_type, image_mime_type) AS attachment_mime_type,
                   attachment_text, extracted_json, analysis_status, created_at, demo_key
            FROM reports
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def seed_fictional_demo(
    database_path: str | Path,
    csv_path: str | Path,
) -> int:
    seeded = 0
    with Path(csv_path).open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            demo_key = row["report_key"]
            original_text = f"SIMULATION ONLY [{demo_key}]: {row['report_text']}"
            with _connection(database_path) as connection:
                existing = connection.execute(
                    "SELECT id, analysis_status FROM reports WHERE demo_key = ?",
                    (demo_key,),
                ).fetchone()

            if existing and existing["analysis_status"] == "Complete":
                continue
            report_id = existing["id"] if existing else save_report(
                database_path,
                original_text,
                demo_key=demo_key,
            )
            extraction = IncidentExtraction(
                category=row["category"],
                description=row["description"],
                location=row["location"],
                latitude=float(row["latitude"]) if row["latitude"] else None,
                longitude=float(row["longitude"]) if row["longitude"] else None,
                reported_at=row["reported_at"] or None,
                affected_count=int(row["affected_count"]) if row["affected_count"] else None,
                injuries_count=int(row["injuries_count"]) if row["injuries_count"] else None,
                fatalities_count=int(row["fatalities_count"]) if row["fatalities_count"] else None,
                infrastructure_damage=json.loads(row["infrastructure_damage_json"]),
                potential_hazards=json.loads(row["potential_hazards_json"]),
                supporting_evidence=json.loads(row["supporting_evidence_json"]),
                uncertainty=json.loads(row["uncertainty_json"]),
            )
            complete_report_analysis(
                database_path,
                report_id,
                extraction,
                assess_priority(extraction),
            )
            seeded += 1
    return seeded


def fetch_report_summaries(database_path: str | Path) -> list[ReportSummary]:
    summaries: list[ReportSummary] = []
    for report in fetch_reports(database_path):
        if not report["extracted_json"]:
            continue
        extracted = json.loads(report["extracted_json"])
        summaries.append(
            ReportSummary(
                report_id=report["id"],
                category=extracted["category"],
                description=extracted["description"],
                location=extracted.get("location"),
            )
        )
    return summaries


def set_report_relationship(
    database_path: str | Path,
    report_id: str,
    related_report_id: str,
    status: str,
    *,
    reviewed_by: str = "Operator",
) -> None:
    if report_id == related_report_id:
        raise ValueError("A report cannot be related to itself.")
    if status not in {"Confirmed related", "Rejected"}:
        raise ValueError("Relationship status must be Confirmed related or Rejected.")
    report_a, report_b = sorted((report_id, related_report_id))
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO report_relationships (
                report_id_a, report_id_b, status, reviewed_by, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_id_a, report_id_b) DO UPDATE SET
                status = excluded.status,
                reviewed_by = excluded.reviewed_by,
                updated_at = excluded.updated_at
            """,
            (report_a, report_b, status, reviewed_by, _now()),
        )
        connection.execute(
            """
            INSERT INTO activity_logs (report_id, action, actor, details_json, created_at)
            VALUES (?, 'Report relationship reviewed', ?, ?, ?)
            """,
            (
                report_id,
                reviewed_by,
                json.dumps(
                    {"related_report_id": related_report_id, "status": status},
                    ensure_ascii=True,
                ),
                _now(),
            ),
        )


def fetch_report_relationships(database_path: str | Path) -> dict[tuple[str, str], str]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            "SELECT report_id_a, report_id_b, status FROM report_relationships"
        ).fetchall()
    return {
        (row["report_id_a"], row["report_id_b"]): row["status"] for row in rows
    }


def update_incident_status(
    database_path: str | Path,
    incident_id: int,
    status: str,
    *,
    actor: str = "Operator",
) -> None:
    if status not in INCIDENT_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(INCIDENT_STATUSES)}")
    with _connection(database_path) as connection:
        result = connection.execute(
            "UPDATE incidents SET status = ? WHERE id = ?",
            (status, incident_id),
        )
        if result.rowcount == 0:
            raise ValueError("Incident was not found.")
        report = connection.execute(
            "SELECT report_id FROM incident_metadata WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO activity_logs (report_id, incident_id, action, actor, details_json, created_at)
            VALUES (?, ?, 'Incident status changed', ?, ?, ?)
            """,
            (
                report["report_id"] if report else None,
                incident_id,
                actor,
                json.dumps({"status": status}, ensure_ascii=True),
                _now(),
            ),
        )


def fetch_activity_log(database_path: str | Path, limit: int = 100) -> list[dict]:
    if limit < 1:
        raise ValueError("limit must be positive.")
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, report_id, incident_id, action, actor, details_json, created_at
            FROM activity_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _validated_coordinates(latitude: float, longitude: float) -> tuple[float, float]:
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter valid latitude and longitude values.") from error
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise ValueError("Coordinates must be within valid latitude/longitude ranges.")
    return latitude, longitude


def add_response_team(
    database_path: str | Path,
    name: str,
    base_location: str,
    latitude: float,
    longitude: float,
    *,
    notes: str = "",
    actor: str = "Operator",
) -> int:
    name = name.strip()
    base_location = base_location.strip()
    if not name or not base_location:
        raise ValueError("Team name and base location are required.")
    latitude, longitude = _validated_coordinates(latitude, longitude)
    now = _now()
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO response_teams (
                name, base_location, latitude, longitude, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, base_location, latitude, longitude, notes.strip(), now, now),
        )
        team_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_logs (action, actor, details_json, created_at)
            VALUES ('Response team added', ?, ?, ?)
            """,
            (actor, json.dumps({"team_id": team_id, "name": name}), now),
        )
    return team_id


def fetch_response_teams(database_path: str | Path) -> list[dict]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, name, base_location, latitude, longitude, status, notes,
                   created_at, updated_at
            FROM response_teams
            ORDER BY name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def add_resource(
    database_path: str | Path,
    name: str,
    resource_type: str,
    quantity_total: int,
    *,
    actor: str = "Operator",
) -> int:
    name = name.strip()
    resource_type = resource_type.strip()
    if not name or not resource_type:
        raise ValueError("Resource name and type are required.")
    if type(quantity_total) is not int or quantity_total < 1:
        raise ValueError("Resource quantity must be a positive whole number.")
    now = _now()
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO resources (
                name, resource_type, quantity_total, quantity_available,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, resource_type, quantity_total, quantity_total, now, now),
        )
        resource_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_logs (action, actor, details_json, created_at)
            VALUES ('Resource inventory added', ?, ?, ?)
            """,
            (
                actor,
                json.dumps(
                    {"resource_id": resource_id, "name": name, "quantity": quantity_total}
                ),
                now,
            ),
        )
    return resource_id


def fetch_resources(database_path: str | Path) -> list[dict]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, name, resource_type, quantity_total, quantity_available,
                   created_at, updated_at
            FROM resources
            ORDER BY resource_type, name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def assign_response_team(
    database_path: str | Path,
    incident_id: int,
    team_id: int,
    *,
    resource_id: int | None = None,
    resource_quantity: int = 0,
    actor: str = "Operator",
) -> int:
    if type(resource_quantity) is not int or resource_quantity < 0:
        raise ValueError("Resource quantity must be a non-negative whole number.")
    if (resource_id is None and resource_quantity != 0) or (
        resource_id is not None and resource_quantity < 1
    ):
        raise ValueError("Choose a resource and positive quantity, or no resource.")
    now = _now()
    with _connection(database_path) as connection:
        incident = connection.execute(
            "SELECT status FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if incident is None:
            raise ValueError("Incident was not found.")
        if incident["status"] in {"Resolved", "Closed"}:
            raise ValueError("A resolved or closed incident cannot receive an assignment.")
        team = connection.execute(
            "SELECT status FROM response_teams WHERE id = ?", (team_id,)
        ).fetchone()
        if team is None:
            raise ValueError("Response team was not found.")
        if team["status"] != "Available":
            raise ValueError("Response team already has an active assignment.")

        if resource_id is not None:
            resource = connection.execute(
                "SELECT quantity_available FROM resources WHERE id = ?",
                (resource_id,),
            ).fetchone()
            if resource is None:
                raise ValueError("Resource was not found.")
            if resource["quantity_available"] < resource_quantity:
                raise ValueError("Not enough units of this resource are available.")
            connection.execute(
                """
                UPDATE resources
                SET quantity_available = quantity_available - ?, updated_at = ?
                WHERE id = ?
                """,
                (resource_quantity, now, resource_id),
            )

        cursor = connection.execute(
            """
            INSERT INTO resource_assignments (
                incident_id, team_id, resource_id, resource_quantity, assigned_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                team_id,
                resource_id,
                resource_quantity,
                actor,
                now,
                now,
            ),
        )
        assignment_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE response_teams SET status = 'Assigned', updated_at = ? WHERE id = ?",
            (now, team_id),
        )
        connection.execute(
            """
            INSERT INTO activity_logs (incident_id, action, actor, details_json, created_at)
            VALUES (?, 'Response team assigned (simulation only)', ?, ?, ?)
            """,
            (
                incident_id,
                actor,
                json.dumps(
                    {
                        "assignment_id": assignment_id,
                        "team_id": team_id,
                        "resource_id": resource_id,
                        "resource_quantity": resource_quantity,
                    }
                ),
                now,
            ),
        )
    return assignment_id


def return_response_assignment(
    database_path: str | Path,
    assignment_id: int,
    *,
    actor: str = "Operator",
) -> None:
    now = _now()
    with _connection(database_path) as connection:
        assignment = connection.execute(
            """
            SELECT incident_id, team_id, resource_id, resource_quantity, status
            FROM resource_assignments WHERE id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if assignment is None:
            raise ValueError("Assignment was not found.")
        if assignment["status"] != "Active":
            raise ValueError("Assignment is already returned.")

        result = connection.execute(
            """
            UPDATE resource_assignments SET status = 'Returned', updated_at = ?
            WHERE id = ? AND status = 'Active'
            """,
            (now, assignment_id),
        )
        if result.rowcount == 0:
            raise ValueError("Assignment is already returned.")
        connection.execute(
            "UPDATE response_teams SET status = 'Available', updated_at = ? WHERE id = ?",
            (now, assignment["team_id"]),
        )
        if assignment["resource_id"] is not None:
            connection.execute(
                """
                UPDATE resources
                SET quantity_available = quantity_available + ?, updated_at = ?
                WHERE id = ?
                """,
                (assignment["resource_quantity"], now, assignment["resource_id"]),
            )
        connection.execute(
            """
            INSERT INTO activity_logs (incident_id, action, actor, details_json, created_at)
            VALUES (?, 'Response team returned (simulation only)', ?, ?, ?)
            """,
            (
                assignment["incident_id"],
                actor,
                json.dumps({"assignment_id": assignment_id}),
                now,
            ),
        )


def fetch_resource_assignments(database_path: str | Path) -> list[dict]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT a.id, a.incident_id, i.description AS incident_description,
                   a.team_id, t.name AS team_name, t.latitude AS team_latitude,
                   t.longitude AS team_longitude, a.resource_id, r.name AS resource_name,
                   a.resource_quantity, a.status, a.assigned_by, a.created_at,
                   a.updated_at, m.latitude AS incident_latitude,
                   m.longitude AS incident_longitude
            FROM resource_assignments AS a
            JOIN incidents AS i ON i.id = a.incident_id
            JOIN response_teams AS t ON t.id = a.team_id
            LEFT JOIN resources AS r ON r.id = a.resource_id
            LEFT JOIN incident_metadata AS m ON m.incident_id = a.incident_id
            ORDER BY a.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def add_simulation_scenario(
    database_path: str | Path,
    name: str,
    scenario_type: str,
    latitude: float,
    longitude: float,
    radius_m: int,
    *,
    notes: str = "",
    actor: str = "Operator",
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Scenario name is required.")
    if scenario_type not in {"Road closure", "Flood zone"}:
        raise ValueError("Scenario type must be Road closure or Flood zone.")
    latitude, longitude = _validated_coordinates(latitude, longitude)
    if type(radius_m) is not int or not 50 <= radius_m <= 50000:
        raise ValueError("Scenario radius must be between 50 and 50,000 metres.")
    now = _now()
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO simulation_scenarios (
                name, scenario_type, latitude, longitude, radius_m, notes,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                scenario_type,
                latitude,
                longitude,
                radius_m,
                notes.strip(),
                actor,
                now,
                now,
            ),
        )
        scenario_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_logs (action, actor, details_json, created_at)
            VALUES ('Hypothetical scenario added', ?, ?, ?)
            """,
            (
                actor,
                json.dumps(
                    {
                        "scenario_id": scenario_id,
                        "name": name,
                        "scenario_type": scenario_type,
                        "active": False,
                    }
                ),
                now,
            ),
        )
    return scenario_id


def set_simulation_scenario_active(
    database_path: str | Path,
    scenario_id: int,
    active: bool,
    *,
    actor: str = "Operator",
) -> None:
    if not isinstance(active, bool):
        raise ValueError("Scenario state must be active or inactive.")
    now = _now()
    with _connection(database_path) as connection:
        result = connection.execute(
            """
            UPDATE simulation_scenarios SET active = ?, updated_at = ? WHERE id = ?
            """,
            (int(active), now, scenario_id),
        )
        if result.rowcount == 0:
            raise ValueError("Scenario was not found.")
        connection.execute(
            """
            INSERT INTO activity_logs (action, actor, details_json, created_at)
            VALUES ('Hypothetical scenario state changed', ?, ?, ?)
            """,
            (
                actor,
                json.dumps({"scenario_id": scenario_id, "active": active}),
                now,
            ),
        )


def fetch_simulation_scenarios(database_path: str | Path) -> list[dict]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, name, scenario_type, latitude, longitude, radius_m, active,
                   notes, created_by, created_at, updated_at
            FROM simulation_scenarios
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


DISPATCH_REQUEST_TYPES = (
    "Response request",
    "Resource request",
    "Follow-up request",
)


def create_dispatch_request(
    database_path: str | Path,
    incident_id: int,
    request_type: str,
    summary: str,
    *,
    assignment_id: int | None = None,
    actor: str = "Operator",
) -> int:
    summary = summary.strip()
    if request_type not in DISPATCH_REQUEST_TYPES:
        raise ValueError("Select a supported request type.")
    if not summary or len(summary) > 2000:
        raise ValueError("Request summary must contain 1 to 2,000 characters.")
    now = _now()
    with _connection(database_path) as connection:
        incident = connection.execute(
            "SELECT status FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if incident is None:
            raise ValueError("Incident was not found.")
        if incident["status"] in {"Resolved", "Closed"}:
            raise ValueError("A resolved or closed incident cannot receive a request.")
        if assignment_id is not None:
            assignment = connection.execute(
                """
                SELECT incident_id, status FROM resource_assignments WHERE id = ?
                """,
                (assignment_id,),
            ).fetchone()
            if assignment is None or assignment["incident_id"] != incident_id:
                raise ValueError("Assignment does not belong to this incident.")
            if assignment["status"] != "Active":
                raise ValueError("Only an active simulated assignment can be referenced.")
        cursor = connection.execute(
            """
            INSERT INTO dispatch_requests (
                incident_id, assignment_id, request_type, summary, status,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'Pending approval', ?, ?, ?)
            """,
            (
                incident_id,
                assignment_id,
                request_type,
                summary,
                actor,
                now,
                now,
            ),
        )
        request_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_logs (incident_id, action, actor, details_json, created_at)
            VALUES (?, 'Local dispatch request drafted', ?, ?, ?)
            """,
            (
                incident_id,
                actor,
                json.dumps(
                    {
                        "request_id": request_id,
                        "request_type": request_type,
                        "assignment_id": assignment_id,
                    },
                    ensure_ascii=True,
                ),
                now,
            ),
        )
    return request_id


def review_dispatch_request(
    database_path: str | Path,
    request_id: int,
    decision: str,
    *,
    actor: str,
) -> None:
    if decision not in {"approve", "cancel"}:
        raise ValueError("Decision must be approve or cancel.")
    now = _now()
    status = "Approved (not sent)" if decision == "approve" else "Cancelled"
    with _connection(database_path) as connection:
        request = connection.execute(
            "SELECT incident_id, status FROM dispatch_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        if request is None:
            raise ValueError("Dispatch request was not found.")
        if request["status"] != "Pending approval":
            raise ValueError("Only pending requests can be reviewed.")
        result = connection.execute(
            """
            UPDATE dispatch_requests
            SET status = ?, approved_by = ?, approved_at = ?, updated_at = ?
            WHERE id = ? AND status = 'Pending approval'
            """,
            (
                status,
                actor if decision == "approve" else None,
                now if decision == "approve" else None,
                now,
                request_id,
            ),
        )
        if result.rowcount == 0:
            raise ValueError("Dispatch request has already been reviewed.")
        connection.execute(
            """
            INSERT INTO activity_logs (incident_id, action, actor, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                request["incident_id"],
                "Local dispatch request approved; not sent"
                if decision == "approve"
                else "Local dispatch request cancelled",
                actor,
                json.dumps({"request_id": request_id}, ensure_ascii=True),
                now,
            ),
        )


def fetch_dispatch_requests(database_path: str | Path) -> list[dict]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT d.id, d.incident_id, i.description AS incident_description,
                   d.assignment_id, d.request_type, d.summary, d.status,
                   d.created_by, d.approved_by, d.created_at, d.updated_at,
                   d.approved_at
            FROM dispatch_requests AS d
            JOIN incidents AS i ON i.id = d.incident_id
            ORDER BY d.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_situation_report_draft(
    database_path: str | Path,
    content: str,
    *,
    actor: str,
) -> int:
    content = content.strip()
    if not content or len(content) > 20_000:
        raise ValueError("Situation report must contain 1 to 20,000 characters.")
    now = _now()
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO situation_reports (content, generated_by, generated_at)
            VALUES (?, ?, ?)
            """,
            (content, actor, now),
        )
        report_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO activity_logs (action, actor, details_json, created_at)
            VALUES ('AI situation report draft generated', ?, ?, ?)
            """,
            (
                actor,
                json.dumps({"situation_report_id": report_id}),
                now,
            ),
        )
    return report_id


def fetch_situation_reports(
    database_path: str | Path,
    limit: int = 20,
) -> list[dict]:
    if limit < 1:
        raise ValueError("limit must be positive.")
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, content, review_status, generated_by, generated_at,
                   reviewed_by, reviewed_at
            FROM situation_reports
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def review_situation_report(
    database_path: str | Path,
    report_id: int,
    content: str,
    *,
    reviewer: str,
) -> None:
    content = content.strip()
    if not content or len(content) > 20_000:
        raise ValueError("Reviewed report must contain 1 to 20,000 characters.")
    now = _now()
    with _connection(database_path) as connection:
        result = connection.execute(
            """
            UPDATE situation_reports
            SET content = ?, review_status = 'Reviewed', reviewed_by = ?,
                reviewed_at = ?
            WHERE id = ? AND review_status = 'Draft'
            """,
            (content, reviewer, now, report_id),
        )
        if result.rowcount == 0:
            raise ValueError("Draft was not found or has already been reviewed.")
        connection.execute(
            """
            INSERT INTO activity_logs (action, actor, details_json, created_at)
            VALUES ('AI situation report reviewed', ?, ?, ?)
            """,
            (
                reviewer,
                json.dumps({"situation_report_id": report_id}),
                now,
            ),
        )
