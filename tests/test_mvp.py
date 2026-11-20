from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sqlite3
from io import BytesIO
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave

from google.genai import errors
from pypdf import PdfWriter
from core.correlation_engine import ReportSummary, suggest_duplicates
from core.gemini_client import GeminiClient, GeminiRequestError
from core.incident_classifier import IncidentExtraction, analyze_emergency_report
from core.local_transcription import (
    AudioTranscriptionError,
    MAX_AUDIO_BYTES,
    transcribe_audio,
)
from core.priority_engine import assess_priority
from database.connection import fetch_incidents, init_database, insert_incidents
from database.operations import (
    add_resource,
    add_response_team,
    add_simulation_scenario,
    assign_response_team,
    complete_report_analysis,
    create_dispatch_request,
    create_situation_report_draft,
    fetch_activity_log,
    fetch_dispatch_requests,
    fetch_situation_reports,
    fetch_resources,
    fetch_resource_assignments,
    fetch_report_summaries,
    fetch_report_relationships,
    fetch_reports,
    fetch_response_teams,
    fetch_simulation_scenarios,
    mark_report_analysis_failed,
    return_response_assignment,
    review_dispatch_request,
    review_situation_report,
    save_report,
    seed_fictional_demo,
    set_report_relationship,
    set_simulation_scenario_active,
    update_incident_status,
    update_report_attachment_text,
)
from modules.authentication import password_matches
from modules.digital_twin import haversine_km
from modules.report_center import PdfInputError, _ocr_pdf_pages, extract_pdf_text


class ProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "test.db"
        init_database(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()


class IncidentIntelligenceTests(unittest.TestCase):
    def test_gemini_retries_temporary_failures_a_bounded_number_of_times(self):
        client = GeminiClient(api_key="test-key", model="test-model")
        client._client = Mock()
        client._client.models.generate_content.side_effect = [
            errors.APIError(503, {"message": "temporary"}),
            Mock(text="OK"),
        ]

        with patch("core.gemini_client.time.sleep") as sleep:
            response = client.generate("Reply OK.")

        self.assertEqual(response, "OK")
        self.assertEqual(client._client.models.generate_content.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_gemini_errors_do_not_expose_the_api_key(self):
        secret = "test-key-not-for-output"
        client = GeminiClient(api_key=secret, model="test-model")
        client._client = Mock()
        client._client.models.generate_content.side_effect = errors.APIError(
            400,
            {"message": f"invalid key {secret}"},
        )

        with self.assertRaises(GeminiRequestError) as raised:
            client.generate("Reply OK.")

        self.assertNotIn(secret, str(raised.exception))

    def test_priority_explains_reported_fatalities(self):
        extraction = IncidentExtraction(
            category="Road accident",
            description="A fictional report says one person died.",
            fatalities_count=1,
        )

        priority = assess_priority(extraction)

        self.assertEqual(priority.suggested_priority, "Critical")
        self.assertTrue(any("fatality" in factor for factor in priority.factors))
        self.assertIn("location", priority.missing_information)
        self.assertIn("unvalidated", priority.explanation)

    def test_duplicate_suggestions_do_not_merge_reports(self):
        first = ReportSummary("one", "Road accident", "Vehicle collision", "Road A")
        second = ReportSummary("two", "Road accident", "Vehicle collision", "Road A")

        suggestions = suggest_duplicates(first, [second])

        self.assertEqual([item.report_id for item in suggestions], ["two"])
        self.assertEqual(first.report_id, "one")
        self.assertEqual(second.report_id, "two")

    def test_extraction_discards_unsubstantiated_fields(self):
        proposed = IncidentExtraction(
            category="Road accident",
            description="A reported collision.",
            location="Invented location",
            latitude=34.0,
            longitude=71.5,
            reported_at="2026-01-01",
            affected_count=3,
            injuries_count=2,
            fatalities_count=1,
        )
        client = Mock()
        client.generate_json.return_value = proposed

        extraction = analyze_emergency_report(
            client,
            "A vehicle collision was reported.",
        )

        self.assertIsNone(extraction.location)
        self.assertIsNone(extraction.latitude)
        self.assertIsNone(extraction.longitude)
        self.assertIsNone(extraction.reported_at)
        self.assertIsNone(extraction.affected_count)
        self.assertIsNone(extraction.injuries_count)
        self.assertIsNone(extraction.fatalities_count)
        self.assertGreaterEqual(len(extraction.uncertainty), 4)

    def test_extraction_requires_text_or_image(self):
        with self.assertRaises(ValueError):
            analyze_emergency_report(Mock(), "  ")


class AdditionalFeatureTests(unittest.TestCase):
    def test_operator_password_comparison_fails_closed(self):
        self.assertTrue(password_matches("correct horse", "correct horse"))
        self.assertFalse(password_matches("", "correct horse"))
        self.assertFalse(password_matches("wrong", "correct horse"))
        self.assertFalse(password_matches("anything", ""))

    def test_pdf_text_extraction_accepts_selectable_text_and_rejects_scans(self):
        page = Mock()
        page.extract_text.return_value = "A fictional road blockage was reported."
        reader = Mock(is_encrypted=False, pages=[page])
        with patch("modules.report_center.PdfReader", return_value=reader):
            self.assertEqual(
                extract_pdf_text(b"pdf-bytes"),
                "A fictional road blockage was reported.",
            )

        page.extract_text.return_value = " "
        with (
            patch("modules.report_center.PdfReader", return_value=reader),
            patch("modules.report_center._ocr_pdf_pages", return_value=["OCR text"]),
        ):
            self.assertEqual(extract_pdf_text(b"scanned-pdf"), "OCR text")
        with (
            patch("modules.report_center.PdfReader", return_value=reader),
            patch("modules.report_center._ocr_pdf_pages", return_value=[" "]),
        ):
            with self.assertRaisesRegex(PdfInputError, "No readable text"):
                extract_pdf_text(b"scanned-pdf")

    def test_pdf_text_extraction_rejects_encrypted_and_oversized_documents(self):
        encrypted = Mock(is_encrypted=True)
        with patch("modules.report_center.PdfReader", return_value=encrypted):
            with self.assertRaisesRegex(PdfInputError, "Encrypted"):
                extract_pdf_text(b"encrypted-pdf")

        with self.assertRaisesRegex(PdfInputError, "8 MB"):
            extract_pdf_text(b"x" * (8 * 1024 * 1024 + 1))

    def test_pdf_page_and_extracted_text_limits_are_enforced(self):
        page = Mock()
        page.extract_text.return_value = "x"
        reader = Mock(is_encrypted=False, pages=[page] * 21)
        with patch("modules.report_center.PdfReader", return_value=reader):
            with self.assertRaisesRegex(PdfInputError, "20 pages"):
                extract_pdf_text(b"too-many-pages")

        page.extract_text.return_value = "x" * 30_001
        reader.pages = [page]
        with patch("modules.report_center.PdfReader", return_value=reader):
            with self.assertRaisesRegex(PdfInputError, "30,000-character"):
                extract_pdf_text(b"too-much-text")

    def test_pdf_parser_sends_real_scanned_style_pdf_pages_to_local_ocr(self):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        output = BytesIO()
        writer.write(output)

        with patch(
            "modules.report_center._ocr_pdf_pages",
            return_value=["Local OCR recognized a fictional blockage."],
        ) as ocr:
            text = extract_pdf_text(output.getvalue())

        self.assertIn("fictional blockage", text)
        ocr.assert_called_once_with(output.getvalue(), [0])

    def test_local_ocr_uses_both_english_and_urdu_language_data(self):
        import pymupdf
        import pytesseract

        page = Mock()
        rendered = Mock()
        rendered.tobytes.return_value = b"png bytes"
        page.get_pixmap.return_value = rendered
        document = Mock()
        document.load_page.return_value = page
        with (
            patch("pymupdf.open", return_value=document),
            patch("pytesseract.get_languages", return_value=["eng", "urd"]),
            patch("pytesseract.image_to_string", return_value="متن recognized") as ocr,
            patch("PIL.Image.open", return_value=object()),
        ):
            result = _ocr_pdf_pages(b"pdf bytes", [0])

        self.assertEqual(result, ["متن recognized"])
        self.assertEqual(ocr.call_args.kwargs["lang"], "eng+urd")
        document.close.assert_called_once()

    def test_local_ocr_reports_missing_urdu_language_data(self):
        import pytesseract

        with patch("pytesseract.get_languages", return_value=["eng"]):
            with self.assertRaisesRegex(PdfInputError, "English and Urdu"):
                _ocr_pdf_pages(b"pdf bytes", [0])

    def test_local_transcription_uses_a_multilingual_model_without_uploading_audio(self):
        wav_bytes = BytesIO()
        with wave.open(wav_bytes, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(b"\0\0" * 16_000)
        model = Mock()
        model.transcribe.return_value = (
            [SimpleNamespace(text=" A fictional road blockage was reported. ")],
            SimpleNamespace(language="ur"),
        )

        with patch("core.local_transcription._load_model", return_value=model):
            transcript, language = transcribe_audio(wav_bytes.getvalue(), "report.wav")

        self.assertEqual(transcript, "A fictional road blockage was reported.")
        self.assertEqual(language, "ur")
        self.assertEqual(model.transcribe.call_args.kwargs["language"], None)

    def test_local_transcription_rejects_oversized_and_unsupported_audio(self):
        with self.assertRaisesRegex(AudioTranscriptionError, "15 MB"):
            transcribe_audio(b"x" * (MAX_AUDIO_BYTES + 1), "report.wav")
        with self.assertRaisesRegex(AudioTranscriptionError, "Use a FLAC"):
            transcribe_audio(b"audio", "report.exe")
        with self.assertRaisesRegex(AudioTranscriptionError, "could not be decoded"):
            transcribe_audio(b"not a wav file", "report.wav")

    def test_haversine_uses_distance_not_coordinate_units(self):
        self.assertAlmostEqual(haversine_km(0, 0, 0, 1), 111.195, places=2)


class DatabaseWorkflowTests(ProjectTestCase):
    def test_failed_analysis_keeps_original_report(self):
        original = "Simulation only: caller reports a blocked road."
        report_id = save_report(self.database_path, original)

        mark_report_analysis_failed(self.database_path, report_id)

        reports = fetch_reports(self.database_path)
        self.assertEqual(reports[0]["original_text"], original)
        self.assertEqual(reports[0]["analysis_status"], "Failed")
        self.assertEqual(fetch_incidents(self.database_path), [])

    def test_completed_report_status_change_and_relationship_are_audited(self):
        original = "Simulation only: a vehicle collision at Road A."
        first_id = save_report(self.database_path, original, demo_key="first")
        second_id = save_report(
            self.database_path,
            "Simulation only: another collision at Road A.",
            demo_key="second",
        )
        extraction = IncidentExtraction(
            category="Road accident",
            description="Vehicle collision at Road A.",
            location="Road A",
            reported_at="2026-11-19 08:15",
            injuries_count=1,
        )
        incident_id = complete_report_analysis(
            self.database_path,
            first_id,
            extraction,
            assess_priority(extraction),
        )
        set_report_relationship(
            self.database_path,
            first_id,
            second_id,
            "Confirmed related",
        )
        update_incident_status(
            self.database_path,
            incident_id,
            "Verified",
        )

        pair = tuple(sorted((first_id, second_id)))
        self.assertEqual(fetch_report_relationships(self.database_path)[pair], "Confirmed related")
        self.assertEqual(fetch_incidents(self.database_path)[0]["status"], "Verified")
        self.assertGreaterEqual(len(fetch_activity_log(self.database_path)), 4)

    def test_fictional_demo_is_idempotent_and_suggests_duplicates(self):
        sample_path = (
            Path(__file__).resolve().parents[1]
            / "sample_data"
            / "fictional_peshawar_reports.csv"
        )

        first_load = seed_fictional_demo(self.database_path, sample_path)
        second_load = seed_fictional_demo(self.database_path, sample_path)

        self.assertEqual(first_load, 5)
        self.assertEqual(second_load, 0)
        incidents = fetch_incidents(self.database_path)
        self.assertEqual(len(incidents), 5)
        self.assertTrue(
            all(item["uncertainty_json"].find("Fictional demonstration data only.") >= 0
                for item in incidents)
        )
        reports = fetch_reports(self.database_path)
        report_ids = {report["demo_key"]: report["id"] for report in reports}
        summaries = fetch_report_summaries(self.database_path)
        primary = next(
            summary
            for summary in summaries
            if summary.report_id == report_ids["accident-primary"]
        )
        suggestions = suggest_duplicates(
            primary,
            [
                summary
                for summary in summaries
                if summary.report_id != primary.report_id
            ],
        )
        self.assertIn(
            report_ids["accident-repeat"],
            [suggestion.report_id for suggestion in suggestions],
        )

    def test_additive_report_migration_preserves_existing_report(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE reports (
                        id TEXT PRIMARY KEY, original_text TEXT NOT NULL,
                        image_filename TEXT, image_mime_type TEXT, image_data BLOB,
                        extracted_json TEXT, analysis_status TEXT NOT NULL DEFAULT 'Pending',
                        created_at TEXT NOT NULL, demo_key TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO reports (id, original_text, created_at) VALUES (?, ?, ?)",
                    ("legacy-report", "Preserved legacy report", "2026-01-01T00:00:00+00:00"),
                )

        init_database(legacy_path)

        report = fetch_reports(legacy_path)[0]
        self.assertEqual(report["id"], "legacy-report")
        self.assertEqual(report["original_text"], "Preserved legacy report")
        self.assertEqual(report["source_type"], "Text")

    def test_pdf_attachment_and_voice_transcript_source_are_persisted(self):
        pdf_id = save_report(
            self.database_path,
            "",
            source_type="PDF",
            attachment_filename="incident.pdf",
            attachment_mime_type="application/pdf",
            attachment_data=b"pdf bytes",
            attachment_text="Selectable report content",
        )
        voice_id = save_report(
            self.database_path,
            "Operator-reviewed transcript",
            source_type="Voice transcript",
        )

        reports = {report["id"]: report for report in fetch_reports(self.database_path)}
        self.assertEqual(reports[pdf_id]["source_type"], "PDF")
        self.assertEqual(reports[pdf_id]["attachment_filename"], "incident.pdf")
        self.assertEqual(reports[pdf_id]["attachment_text"], "Selectable report content")
        self.assertEqual(reports[voice_id]["source_type"], "Voice transcript")

    def test_local_audio_transcript_is_persisted_and_audited(self):
        report_id = save_report(
            self.database_path,
            "",
            source_type="Voice recording",
            attachment_filename="recording.wav",
            attachment_mime_type="audio/wav",
            attachment_data=b"local audio bytes",
            actor="Test operator",
        )

        update_report_attachment_text(
            self.database_path,
            report_id,
            "Operator-relevant local transcript.",
            actor="Test operator",
        )

        report = fetch_reports(self.database_path)[0]
        self.assertEqual(report["source_type"], "Voice recording")
        self.assertEqual(
            report["attachment_text"],
            "Operator-relevant local transcript.",
        )
        self.assertTrue(
            any(
                entry["action"] == "Voice audio transcribed locally"
                for entry in fetch_activity_log(self.database_path)
            )
        )

    def test_resource_assignment_reserves_and_returns_inventory_atomically(self):
        insert_incidents(
            self.database_path,
            [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "source": "test",
                    "severity": "High",
                    "status": "New",
                    "description": "Fictional road blockage",
                }
            ],
        )
        incident_id = fetch_incidents(self.database_path)[0]["id"]
        team_id = add_response_team(
            self.database_path,
            "North team",
            "Declared base",
            34.0,
            71.0,
        )
        resource_id = add_resource(self.database_path, "Medical kits", "Medical", 3)

        with self.assertRaisesRegex(ValueError, "Not enough"):
            assign_response_team(
                self.database_path,
                incident_id,
                team_id,
                resource_id=resource_id,
                resource_quantity=4,
            )
        self.assertEqual(fetch_resources(self.database_path)[0]["quantity_available"], 3)
        self.assertEqual(fetch_response_teams(self.database_path)[0]["status"], "Available")

        assignment_id = assign_response_team(
            self.database_path,
            incident_id,
            team_id,
            resource_id=resource_id,
            resource_quantity=2,
            actor="Test operator",
        )
        self.assertEqual(fetch_resources(self.database_path)[0]["quantity_available"], 1)
        self.assertEqual(fetch_response_teams(self.database_path)[0]["status"], "Assigned")
        with self.assertRaisesRegex(ValueError, "active assignment"):
            assign_response_team(self.database_path, incident_id, team_id)

        return_response_assignment(self.database_path, assignment_id, actor="Test operator")

        self.assertEqual(fetch_resources(self.database_path)[0]["quantity_available"], 3)
        self.assertEqual(fetch_response_teams(self.database_path)[0]["status"], "Available")
        self.assertEqual(fetch_resource_assignments(self.database_path)[0]["status"], "Returned")
        with self.assertRaisesRegex(ValueError, "already returned"):
            return_response_assignment(self.database_path, assignment_id)

        update_incident_status(self.database_path, incident_id, "Resolved")
        with self.assertRaisesRegex(ValueError, "resolved or closed"):
            assign_response_team(self.database_path, incident_id, team_id)

        self.assertTrue(
            any(entry["actor"] == "Test operator" for entry in fetch_activity_log(self.database_path))
        )

    def test_simulation_scenario_starts_inactive_and_is_audited(self):
        scenario_id = add_simulation_scenario(
            self.database_path,
            "Hypothetical flood",
            "Flood zone",
            34.0,
            71.0,
            500,
            actor="Test operator",
        )
        scenario = fetch_simulation_scenarios(self.database_path)[0]
        self.assertEqual(scenario["id"], scenario_id)
        self.assertEqual(scenario["active"], 0)

        set_simulation_scenario_active(
            self.database_path,
            scenario_id,
            True,
            actor="Test operator",
        )

        self.assertEqual(fetch_simulation_scenarios(self.database_path)[0]["active"], 1)
        self.assertTrue(
            any(
                entry["action"] == "Hypothetical scenario state changed"
                for entry in fetch_activity_log(self.database_path)
            )
        )

    def test_local_dispatch_queue_requires_review_and_never_sends(self):
        insert_incidents(
            self.database_path,
            [
                {
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "source": "test",
                    "severity": "High",
                    "status": "New",
                    "description": "Fictional road blockage",
                }
            ],
        )
        incident_id = fetch_incidents(self.database_path)[0]["id"]
        request_id = create_dispatch_request(
            self.database_path,
            incident_id,
            "Response request",
            "Fictional request for operator review.",
            actor="Draft operator",
        )
        request = fetch_dispatch_requests(self.database_path)[0]
        self.assertEqual(request["status"], "Pending approval")
        self.assertIsNone(request["approved_by"])

        review_dispatch_request(
            self.database_path,
            request_id,
            "approve",
            actor="Approving operator",
        )
        request = fetch_dispatch_requests(self.database_path)[0]
        self.assertEqual(request["status"], "Approved (not sent)")
        self.assertEqual(request["approved_by"], "Approving operator")
        with self.assertRaisesRegex(ValueError, "Only pending"):
            review_dispatch_request(
                self.database_path,
                request_id,
                "approve",
                actor="Approving operator",
            )
        self.assertTrue(
            any(
                entry["action"] == "Local dispatch request approved; not sent"
                for entry in fetch_activity_log(self.database_path)
            )
        )

    def test_situation_report_is_persisted_and_requires_operator_review(self):
        report_id = create_situation_report_draft(
            self.database_path,
            "AI-generated fictional situation report.",
            actor="Draft operator",
        )
        draft = fetch_situation_reports(self.database_path)[0]
        self.assertEqual(draft["id"], report_id)
        self.assertEqual(draft["review_status"], "Draft")
        self.assertIsNone(draft["reviewed_by"])

        review_situation_report(
            self.database_path,
            report_id,
            "Human-edited and reviewed situation report.",
            reviewer="Review operator",
        )
        reviewed = fetch_situation_reports(self.database_path)[0]
        self.assertEqual(reviewed["review_status"], "Reviewed")
        self.assertEqual(reviewed["content"], "Human-edited and reviewed situation report.")
        self.assertEqual(reviewed["reviewed_by"], "Review operator")
        with self.assertRaisesRegex(ValueError, "already been reviewed"):
            review_situation_report(
                self.database_path,
                report_id,
                "Second review",
                reviewer="Review operator",
            )


if __name__ == "__main__":
    unittest.main()
