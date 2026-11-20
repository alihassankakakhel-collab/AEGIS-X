from __future__ import annotations

import time
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from config import settings


T = TypeVar("T", bound=BaseModel)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class GeminiRequestError(RuntimeError):
    """A safe, user-facing Gemini request failure."""


class GeminiClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else settings.gemini_api_key
        )
        self.model = model if model is not None else settings.gemini_model
        if not self._api_key:
            raise ValueError("GEMINI_API_KEY is missing from .env")
        if not self.model:
            raise ValueError("GEMINI_MODEL is missing from .env")
        self._client = genai.Client(api_key=self._api_key)

    def _generate(
        self,
        contents: str | list[str | types.Part],
        config: types.GenerateContentConfig | None = None,
    ) -> types.GenerateContentResponse:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except errors.APIError as error:
                if (
                    error.code not in TRANSIENT_STATUS_CODES
                    or attempt == MAX_ATTEMPTS - 1
                ):
                    message = str(error).replace(self._api_key, "[REDACTED]")
                    raise GeminiRequestError(
                        f"Gemini request failed (HTTP {error.code}): {message}"
                    ) from error
                time.sleep(0.5 * (2**attempt))

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty.")
        response = self._generate(prompt)
        if not response.text or not response.text.strip():
            raise GeminiRequestError("Gemini returned an empty text response.")
        return response.text.strip()

    def generate_json(
        self,
        contents: str | list[str | types.Part],
        schema: type[T],
    ) -> T:
        response = self._generate(
            contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if not response.text or not response.text.strip():
            raise GeminiRequestError("Gemini returned an empty JSON response.")
        try:
            return schema.model_validate_json(response.text)
        except ValidationError as error:
            raise GeminiRequestError(
                f"Gemini returned JSON that did not match {schema.__name__}."
            ) from error


def get_gemini_client() -> GeminiClient:
    return GeminiClient()


def ask_gemini(prompt: str) -> str:
    return get_gemini_client().generate(prompt)
