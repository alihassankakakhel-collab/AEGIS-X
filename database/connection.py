from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Iterable


def get_connection(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_database(database_path: str | Path) -> None:
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                source TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                description TEXT NOT NULL
            )
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                original_text TEXT NOT NULL,
                image_filename TEXT,
                image_mime_type TEXT,
                image_data BLOB,
                source_type TEXT NOT NULL DEFAULT 'Text',
                attachment_filename TEXT,
                attachment_mime_type TEXT,
                attachment_data BLOB,
                attachment_text TEXT,
                extracted_json TEXT,
                analysis_status TEXT NOT NULL DEFAULT 'Pending',
                created_at TEXT NOT NULL,
                demo_key TEXT
            );

            CREATE TABLE IF NOT EXISTS incident_metadata (
                incident_id INTEGER PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE,
                report_id TEXT UNIQUE REFERENCES reports(id) ON DELETE SET NULL,
                category TEXT,
                location TEXT,
                latitude REAL,
                longitude REAL,
                reported_at TEXT,
                affected_count INTEGER,
                injuries_count INTEGER,
                fatalities_count INTEGER,
                infrastructure_damage_json TEXT NOT NULL DEFAULT '[]',
                potential_hazards_json TEXT NOT NULL DEFAULT '[]',
                supporting_evidence_json TEXT NOT NULL DEFAULT '[]',
                uncertainty_json TEXT NOT NULL DEFAULT '[]',
                priority_score INTEGER NOT NULL DEFAULT 0,
                priority_factors_json TEXT NOT NULL DEFAULT '[]',
                missing_information_json TEXT NOT NULL DEFAULT '[]',
                priority_explanation TEXT
            );

            CREATE TABLE IF NOT EXISTS report_relationships (
                report_id_a TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                report_id_b TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK (status IN ('Confirmed related', 'Rejected')),
                reviewed_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (report_id_a, report_id_b),
                CHECK (report_id_a < report_id_b)
            );

            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id TEXT REFERENCES reports(id) ON DELETE SET NULL,
                incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS response_teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_location TEXT NOT NULL,
                latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
                status TEXT NOT NULL DEFAULT 'Available'
                    CHECK (status IN ('Available', 'Assigned')),
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                resource_type TEXT NOT NULL,
                quantity_total INTEGER NOT NULL CHECK (quantity_total > 0),
                quantity_available INTEGER NOT NULL
                    CHECK (quantity_available >= 0 AND quantity_available <= quantity_total),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resource_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id),
                team_id INTEGER NOT NULL REFERENCES response_teams(id),
                resource_id INTEGER REFERENCES resources(id),
                resource_quantity INTEGER NOT NULL DEFAULT 0 CHECK (resource_quantity >= 0),
                status TEXT NOT NULL DEFAULT 'Active'
                    CHECK (status IN ('Active', 'Returned')),
                assigned_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    (resource_id IS NULL AND resource_quantity = 0)
                    OR (resource_id IS NOT NULL AND resource_quantity > 0)
                )
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_assignment_per_team
                ON resource_assignments(team_id) WHERE status = 'Active';

            CREATE TABLE IF NOT EXISTS simulation_scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                scenario_type TEXT NOT NULL
                    CHECK (scenario_type IN ('Road closure', 'Flood zone')),
                latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
                radius_m INTEGER NOT NULL CHECK (radius_m BETWEEN 50 AND 50000),
                active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
                notes TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dispatch_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id INTEGER NOT NULL REFERENCES incidents(id),
                assignment_id INTEGER REFERENCES resource_assignments(id),
                request_type TEXT NOT NULL
                    CHECK (request_type IN (
                        'Response request', 'Resource request', 'Follow-up request'
                    )),
                summary TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending approval'
                    CHECK (status IN (
                        'Pending approval', 'Approved (not sent)', 'Cancelled'
                    )),
                created_by TEXT NOT NULL,
                approved_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS situation_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'Draft'
                    CHECK (review_status IN ('Draft', 'Reviewed')),
                generated_by TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                reviewed_by TEXT,
                reviewed_at TEXT
            );
            """
        )
        report_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(reports)")
        }
        report_migrations = {
            "demo_key": "TEXT",
            "source_type": "TEXT NOT NULL DEFAULT 'Text'",
            "attachment_filename": "TEXT",
            "attachment_mime_type": "TEXT",
            "attachment_data": "BLOB",
            "attachment_text": "TEXT",
        }
        for column, definition in report_migrations.items():
            if column not in report_columns:
                connection.execute(
                    f"ALTER TABLE reports ADD COLUMN {column} {definition}"
                )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_demo_key "
            "ON reports(demo_key) WHERE demo_key IS NOT NULL"
        )
        connection.commit()


def fetch_incidents(database_path: str | Path) -> list[dict]:
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                incidents.id,
                incidents.timestamp,
                incidents.source,
                incidents.severity,
                incidents.status,
                incidents.description,
                incident_metadata.report_id,
                reports.demo_key,
                incident_metadata.category,
                incident_metadata.location,
                incident_metadata.latitude,
                incident_metadata.longitude,
                incident_metadata.reported_at,
                incident_metadata.affected_count,
                incident_metadata.injuries_count,
                incident_metadata.fatalities_count,
                incident_metadata.infrastructure_damage_json,
                incident_metadata.potential_hazards_json,
                incident_metadata.supporting_evidence_json,
                incident_metadata.uncertainty_json,
                incident_metadata.priority_score,
                incident_metadata.priority_factors_json,
                incident_metadata.missing_information_json,
                incident_metadata.priority_explanation
            FROM incidents
            LEFT JOIN incident_metadata ON incident_metadata.incident_id = incidents.id
            LEFT JOIN reports ON reports.id = incident_metadata.report_id
            ORDER BY incidents.timestamp DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def insert_incidents(
    database_path: str | Path,
    incidents: Iterable[dict],
) -> int:
    records = [
        (
            item["timestamp"],
            item["source"],
            item["severity"],
            item["status"],
            item["description"],
        )
        for item in incidents
    ]
    with closing(get_connection(database_path)) as connection:
        with connection:
            connection.executemany(
                """
                INSERT INTO incidents (timestamp, source, severity, status, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                records,
            )
    return len(records)
