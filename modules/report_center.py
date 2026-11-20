from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sqlite3

import streamlit as st
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from core.correlation_engine import suggest_duplicates
from core.gemini_client import GeminiClient, GeminiRequestError
from core.incident_classifier import analyze_emergency_report
from core.priority_engine import assess_priority
from database.operations import (
    complete_report_analysis,
    fetch_report_relationships,
    fetch_reports,
    fetch_report_summaries,
    mark_report_analysis_failed,
    save_report,
    set_report_relationship,
    update_report_attachment_text,
)
from core.local_transcription import (
    AudioTranscriptionError,
    MAX_AUDIO_BYTES,
    SUPPORTED_AUDIO_SUFFIXES,
    transcribe_audio,
)


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_PDF_TEXT_CHARACTERS = 30_000


class PdfInputError(ValueError):
    pass


def _ocr_pdf_pages(pdf_data: bytes, page_indexes: list[int]) -> list[str]:
    try:
        import pymupdf
        import pytesseract
        from PIL import Image
    except ModuleNotFoundError as error:
        raise PdfInputError(
            "Install the project OCR dependencies to read scanned PDF pages."
        ) from error

    tesseract_command = os.getenv("TESSERACT_CMD")
    if tesseract_command:
        pytesseract.pytesseract.tesseract_cmd = tesseract_command
    try:
        languages = set(pytesseract.get_languages(config=""))
    except pytesseract.TesseractNotFoundError as error:
        raise PdfInputError(
            "Tesseract OCR is not installed or not configured. Install it locally "
            "and add English and Urdu language data."
        ) from error
    missing_languages = {"eng", "urd"} - languages
    if missing_languages:
        raise PdfInputError(
            "Tesseract needs English and Urdu language data (eng and urd) for OCR."
        )

    try:
        document = pymupdf.open(stream=pdf_data, filetype="pdf")
        try:
            pages: list[str] = []
            for page_index in page_indexes:
                page = document.load_page(page_index)
                rendered = page.get_pixmap(
                    matrix=pymupdf.Matrix(2.5, 2.5),
                    alpha=False,
                )
                image = Image.open(BytesIO(rendered.tobytes("png")))
                text = pytesseract.image_to_string(
                    image,
                    lang="eng+urd",
                    config="--psm 11",
                    timeout=30,
                )
                pages.append(text)
            return pages
        finally:
            document.close()
    except pytesseract.TesseractError as error:
        raise PdfInputError("Local OCR could not process a PDF page.") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise PdfInputError("The PDF page could not be rendered for local OCR.") from error


def extract_pdf_text(pdf_data: bytes) -> str:
    if len(pdf_data) > MAX_ATTACHMENT_BYTES:
        raise PdfInputError("PDFs must be 8 MB or smaller.")
    try:
        reader = PdfReader(BytesIO(pdf_data), strict=False)
        if reader.is_encrypted:
            raise PdfInputError("Encrypted PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise PdfInputError(f"PDFs are limited to {MAX_PDF_PAGES} pages.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfInputError:
        raise
    except (PdfReadError, OSError, ValueError) as error:
        raise PdfInputError("The PDF could not be read.") from error

    pages_needing_ocr = [
        index for index, page_text in enumerate(pages) if not page_text.strip()
    ]
    if pages_needing_ocr:
        ocr_pages = _ocr_pdf_pages(pdf_data, pages_needing_ocr)
        for index, ocr_text in zip(pages_needing_ocr, ocr_pages):
            pages[index] = ocr_text

    extracted_text = "\n".join(pages).strip()
    if not extracted_text:
        raise PdfInputError("No readable text was found in the PDF.")
    if len(extracted_text) > MAX_PDF_TEXT_CHARACTERS:
        raise PdfInputError(
            "Extracted PDF text exceeds the 30,000-character limit."
        )
    return extracted_text


def _render_duplicate_suggestions(database_path: Path, actor: str) -> None:
    summaries = fetch_report_summaries(database_path)
    if len(summaries) < 2:
        return

    statuses = fetch_report_relationships(database_path)
    seen: set[tuple[str, str]] = set()
    pending_count = 0
    st.markdown("#### Possible related reports — operator review required")
    for candidate in summaries:
        others = [
            report for report in summaries
            if report.report_id != candidate.report_id
        ]
        for suggestion in suggest_duplicates(candidate, others):
            pair = tuple(sorted((candidate.report_id, suggestion.report_id)))
            if pair in seen:
                continue
            seen.add(pair)
            decision = statuses.get(pair)
            st.write(
                f"Reports `{pair[0]}` and `{pair[1]}` — "
                f"similarity {suggestion.score:.0%}; "
                f"{', '.join(suggestion.reasons)}"
            )
            if decision:
                st.caption(f"Operator decision: {decision}")
                continue

            pending_count += 1
            confirm, reject = st.columns(2)
            if confirm.button("Confirm related", key=f"confirm-{pair[0]}-{pair[1]}"):
                set_report_relationship(
                    database_path,
                    pair[0],
                    pair[1],
                    "Confirmed related",
                    reviewed_by=actor,
                )
                st.rerun()
            if reject.button("Reject suggestion", key=f"reject-{pair[0]}-{pair[1]}"):
                set_report_relationship(
                    database_path,
                    pair[0],
                    pair[1],
                    "Rejected",
                    reviewed_by=actor,
                )
                st.rerun()

    if not seen:
        st.info("No likely duplicate reports found.")
    elif not pending_count:
        st.caption("All current relationship suggestions have an operator decision.")


def _analyze_saved_report(
    database_path: Path,
    report_id: str,
    analysis_text: str,
    *,
    image_bytes: bytes | None,
    image_mime_type: str | None,
) -> None:
    try:
        with st.spinner("Extracting structured report details..."):
            extraction = analyze_emergency_report(
                GeminiClient(),
                analysis_text,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )
            priority = assess_priority(extraction)
            incident_id = complete_report_analysis(
                database_path,
                report_id,
                extraction,
                priority,
            )
    except (GeminiRequestError, ValueError, sqlite3.Error, OSError) as error:
        mark_report_analysis_failed(database_path, report_id)
        st.error(
            "The original report was saved, but AI analysis did not complete. "
            f"Details: {error}"
        )
    else:
        st.success(
            f"Report saved as `{report_id}` and incident #{incident_id} created "
            f"with a suggested {priority.suggested_priority} priority."
        )
        st.json(extraction.model_dump(mode="json"))


def render_report_center(database_path: Path) -> None:
    st.subheader("Submit an emergency report")
    st.caption(
        "Submitted content is retained locally. Gemini analysis is unverified "
        "decision support; it does not dispatch responders. When you select "
        "analyze, report text, extracted PDF text, and any attached photo are "
        "sent to Google Gemini. Audio is transcribed locally; only its transcript "
        "is sent to Gemini."
    )

    with st.form("report-intake", clear_on_submit=True):
        source_type = st.selectbox("Text source", ["Text", "Voice transcript"])
        original_text = st.text_area(
            "Report text or voice transcript",
            placeholder="Enter the report or an operator-reviewed voice transcript.",
            height=130,
        )
        attachment = st.file_uploader(
            "Optional photo evidence or PDF (text or scanned)",
            type=["jpg", "jpeg", "png", "webp", "pdf"],
            accept_multiple_files=False,
        )
        st.caption(
            "Scanned PDF pages use local Tesseract OCR and require English and Urdu data."
        )
        audio_upload = st.file_uploader(
            "Optional voice recording (transcribed locally)",
            type=sorted(suffix.lstrip(".") for suffix in SUPPORTED_AUDIO_SUFFIXES),
            accept_multiple_files=False,
        )
        st.caption(
            "English/Urdu transcription runs on this computer. The multilingual "
            "model is cached locally on first use."
        )
        submitted = st.form_submit_button("Save and analyze report", type="primary")

    if submitted:
        if attachment is not None and audio_upload is not None:
            st.error("Attach one file at a time: a photo/PDF or an audio recording.")
            return
        if not original_text.strip() and attachment is None and audio_upload is None:
            st.error("Enter report text/transcript or attach a photo, PDF, or recording.")
            return

        attachment_data: bytes | None = None
        attachment_mime_type: str | None = None
        attachment_filename: str | None = None
        attachment_text: str | None = None
        image_bytes: bytes | None = None
        image_mime_type: str | None = None
        detected_source_type = source_type
        pdf_error: PdfInputError | None = None
        audio_data: bytes | None = None
        if attachment is not None:
            attachment_data = attachment.getvalue()
            attachment_mime_type = attachment.type
            attachment_filename = Path(attachment.name).name
            if len(attachment_data) > MAX_ATTACHMENT_BYTES:
                st.error("Attachments must be 8 MB or smaller.")
                return
            if attachment_mime_type == "application/pdf":
                detected_source_type = "PDF"
                try:
                    attachment_text = extract_pdf_text(attachment_data)
                except PdfInputError as error:
                    pdf_error = error
            elif attachment_mime_type in ALLOWED_IMAGE_TYPES:
                detected_source_type = "Photo"
                image_bytes = attachment_data
                image_mime_type = attachment_mime_type
            else:
                st.error("Use a JPEG, PNG, WebP image, or PDF.")
                return
        elif audio_upload is not None:
            audio_data = audio_upload.getvalue()
            attachment_filename = Path(audio_upload.name).name
            attachment_mime_type = audio_upload.type or "application/octet-stream"
            if len(audio_data) > MAX_AUDIO_BYTES:
                st.error("Audio recordings must be 15 MB or smaller.")
                return
            if Path(attachment_filename).suffix.casefold() not in SUPPORTED_AUDIO_SUFFIXES:
                st.error("Use a supported FLAC, M4A, MP3, OGG, WAV, or WebM recording.")
                return
            attachment_data = audio_data
            detected_source_type = "Voice recording"

        actor = st.session_state.get("operator_name", "Operator")
        try:
            report_id = save_report(
                database_path,
                original_text.strip(),
                source_type=detected_source_type,
                attachment_filename=attachment_filename,
                attachment_mime_type=attachment_mime_type,
                attachment_data=attachment_data,
                attachment_text=attachment_text,
                actor=actor,
            )
        except sqlite3.Error as error:
            st.error(f"Could not save the original report: {error}")
            return
        except ValueError as error:
            st.error(str(error))
            return

        if pdf_error is not None:
            mark_report_analysis_failed(database_path, report_id)
            st.error(
                f"The original PDF was saved, but extraction failed. {pdf_error}"
            )
        else:
            analysis_text = original_text.strip()
            if audio_data is not None:
                try:
                    with st.spinner("Transcribing audio locally on this computer..."):
                        transcript, detected_language = transcribe_audio(
                            audio_data,
                            attachment_filename or "",
                        )
                    update_report_attachment_text(
                        database_path,
                        report_id,
                        transcript,
                        actor=actor,
                    )
                except (AudioTranscriptionError, sqlite3.Error, ValueError) as error:
                    mark_report_analysis_failed(database_path, report_id)
                    st.error(
                        "The original audio was saved locally, but transcription "
                        f"did not complete. Details: {error}"
                    )
                    return
                analysis_text = (
                    f"{analysis_text}\n\nVoice transcript:\n{transcript}"
                    if analysis_text
                    else transcript
                )
                st.caption(
                    "Detected language: "
                    f"{detected_language or 'unknown'} (local model)"
                )
                st.text_area(
                    "Locally generated transcript",
                    value=transcript,
                    height=120,
                    disabled=True,
                )
            if attachment_text:
                analysis_text = (
                    f"{analysis_text}\n\nPDF extracted text:\n{attachment_text}"
                    if analysis_text
                    else attachment_text
                )
            _analyze_saved_report(
                database_path,
                report_id,
                analysis_text,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            )
    st.divider()
    st.subheader("Submitted reports")
    reports = fetch_reports(database_path)
    if reports:
        st.dataframe(
            [
                {
                    "Report ID": report["id"],
                    "Submitted": report["created_at"],
                    "Source": report["source_type"],
                    "Analysis": report["analysis_status"],
                    "Report text / extracted text": (
                        report["original_text"]
                        or report["attachment_text"]
                        or "No text transcription available"
                    ),
                    "Attachment": report["attachment_filename"] or "None",
                }
                for report in reports
            ],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No submitted reports yet.")

    st.divider()
    _render_duplicate_suggestions(
        database_path,
        st.session_state.get("operator_name", "Operator"),
    )
