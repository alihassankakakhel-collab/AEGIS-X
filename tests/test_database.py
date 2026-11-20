from database.connection import (
    fetch_incidents,
    init_database,
    insert_incidents,
)


def test_database_initializes_and_stores_incidents(tmp_path):
    database_path = tmp_path / "test.db"
    init_database(database_path)

    inserted = insert_incidents(
        database_path,
        [
            {
                "timestamp": "2026-11-19 10:00:00",
                "source": "Test Sensor",
                "severity": "High",
                "status": "Open",
                "description": "Test incident",
            }
        ],
    )

    incidents = fetch_incidents(database_path)
    assert inserted == 1
    assert len(incidents) == 1
    assert incidents[0]["source"] == "Test Sensor"
