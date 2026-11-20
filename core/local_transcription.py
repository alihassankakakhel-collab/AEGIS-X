from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faster_whisper import WhisperModel


MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_AUDIO_SECONDS = 600
MAX_TRANSCRIPT_CHARACTERS = 30_000
SUPPORTED_AUDIO_SUFFIXES = {".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"}


class AudioTranscriptionError(ValueError):
    pass


@lru_cache(maxsize=1)
def _load_model() -> WhisperModel:
    try:
        from huggingface_hub.errors import HfHubHTTPError
        from faster_whisper import WhisperModel
    except ModuleNotFoundError as error:
        raise AudioTranscriptionError(
            "Install the project requirements to enable local audio transcription."
        ) from error

    try:
        return WhisperModel("base", device="cpu", compute_type="int8")
    except (HfHubHTTPError, OSError, RuntimeError, ValueError) as error:
        raise AudioTranscriptionError(
            "Could not load the local multilingual transcription model. "
            "First use may require an internet connection to cache the model."
        ) from error


def transcribe_audio(audio_data: bytes, filename: str) -> tuple[str, str | None]:
    if not audio_data:
        raise AudioTranscriptionError("The audio recording is empty.")
    if len(audio_data) > MAX_AUDIO_BYTES:
        raise AudioTranscriptionError("Audio recordings must be 15 MB or smaller.")
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_AUDIO_SUFFIXES:
        raise AudioTranscriptionError(
            "Use a FLAC, M4A, MP3, OGG, WAV, or WebM audio recording."
        )
    try:
        import av
    except ModuleNotFoundError as error:
        raise AudioTranscriptionError(
            "Install the project requirements to decode local audio."
        ) from error

    audio_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as audio_file:
            audio_path = Path(audio_file.name)
            audio_file.write(audio_data)

        try:
            with av.open(str(audio_path)) as container:
                duration = container.duration
        except av.error.FFmpegError as error:
            raise AudioTranscriptionError("The audio recording could not be decoded.") from error
        if duration is not None and duration / av.time_base > MAX_AUDIO_SECONDS:
            raise AudioTranscriptionError("Audio recordings are limited to 10 minutes.")

        model = _load_model()
        segments, info = model.transcribe(
            str(audio_path),
            language=None,
            beam_size=5,
            vad_filter=True,
        )
        transcript_parts: list[str] = []
        character_count = 0
        for segment in segments:
            text = segment.text.strip()
            character_count += len(text)
            if character_count > MAX_TRANSCRIPT_CHARACTERS:
                raise AudioTranscriptionError(
                    "The transcript exceeds the 30,000-character limit."
                )
            if text:
                transcript_parts.append(text)
        transcript = " ".join(transcript_parts).strip()
        if not transcript:
            raise AudioTranscriptionError(
                "No speech was recognized in the recording."
            )
        return transcript, getattr(info, "language", None)
    except AudioTranscriptionError:
        raise
    except (av.error.FFmpegError, OSError, RuntimeError, ValueError) as error:
        raise AudioTranscriptionError("Local audio transcription failed.") from error
    finally:
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)
