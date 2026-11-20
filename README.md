# AEGIS X

AEGIS X is a demonstration emergency-operations dashboard for report intake,
AI-assisted extraction, operator-reviewed duplicate suggestions, transparent
priority indicators, incident management, resource coordination, hypothetical
scenario comparison, and a local human-reviewed dispatch request queue.

> This is a demonstration and decision-support tool, not an emergency service.
> Reports and AI analysis are unverified. Operators must make all response and
> dispatch decisions.

## Run on Windows

Open the project root in VS Code. Workspace settings select the existing
`venv`; open a new terminal after selecting the interpreter.

Add a strong, private `AEGIS_OPERATOR_PASSWORD` to the existing local `.env`
file. Do not replace an existing Gemini key or copy a placeholder password from
`.env.example` unchanged. The application fails closed until an operator
password is configured.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m streamlit run app.py
```

Set `GEMINI_API_KEY` and, optionally, `GEMINI_MODEL` in `.env`. Never put a real
key or password in `.env.example` or commit `.env`. The default model is
`gemini-3.5-flash-lite`, which should be confirmed for the current API key with:

```powershell
.\venv\Scripts\python.exe .\list_models.py
```

The login is a local, single-operator demonstration gate, not a production
identity or access-management system. Streamlit session state controls login;
there are no separate accounts, roles, or multi-factor authentication.

## Report intake and local OCR/transcription

- Accepts text, operator-reviewed voice transcripts, JPEG/PNG/WebP photos,
  text-based PDFs, and scanned PDFs for local OCR.
- Photo/PDF uploads are limited to 8 MB; PDFs are limited to 20 pages and
  30,000 extracted characters. Local OCR requires Tesseract installed on
  Windows with both English (`eng`) and Urdu (`urd`) language data. If it is
  not on `PATH`, set `TESSERACT_CMD` in `.env` to the local executable path.
  If the language files are in a custom directory, set `TESSDATA_PREFIX` to the
  directory containing the `.traineddata` files. Restart VS Code after changing
  user-level environment variables.
- Accepts FLAC/M4A/MP3/OGG/WAV/WebM recordings up to 15 MB and 10 minutes.
  Transcription uses the local multilingual Whisper `base` model on CPU and
  detects English or Urdu. The model is downloaded to the local cache on first
  use; once cached, transcription runs locally. Audio bytes are retained in the
  local SQLite database and are not sent to Gemini. Only the resulting text is
  sent for AI analysis.
- Pydantic-validated Gemini extraction discards values not supported by source
  facts for coordinates, counts, time, and location. Original reports remain
  stored if OCR, transcription, or AI analysis fails.
- When Gemini analysis is submitted, report text, extracted PDF/voice text, and
  attached photos are sent to Google Gemini. Do not submit sensitive
  information unless your organization has approved that use.

## Coordination, local request queue, and scenario limits

- Team assignments, resource reservations, and returns are operator-controlled
  local simulations. They do not contact or dispatch real responders.
- The Dispatch Queue stores draft requests in SQLite. An operator can approve
  or cancel them; approval records local review only. **No external emergency
  service, messaging provider, or dispatch endpoint is integrated or called.**
- The digital-twin view compares stored incident/team coordinates before and
  after operator-entered hypothetical road-closure or flood-zone circles.
  Geometric overlap is not a forecast or a flood model. Dashed team lines are
  straight-line comparisons, not road routes. Team coordinates are declared
  bases, not live GPS.
- The map does not geocode or infer incident positions. It plots only explicitly
  stored coordinates. Fictional Peshawar demo coordinates are approximate road
  references, not event locations. The map uses OpenStreetMap data; see
  [OpenStreetMap attribution](https://www.openstreetmap.org/copyright).
- SQLite setup uses additive migrations; existing report rows are preserved.

## Tests

Focused, network-free tests use the standard library:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The existing pytest-style tests can also be run after installing the optional
development dependency:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest -q
```
