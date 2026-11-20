import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

APP_NAME = "AEGIS X"
APP_FULL_NAME = "Advanced Emergency Intelligence & Response Ecosystem"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite"
AEGIS_OPERATOR_PASSWORD = os.getenv("AEGIS_OPERATOR_PASSWORD")

_database_path = Path(os.getenv("DATABASE_PATH", "database/aegis_x.db"))
DATABASE_PATH = (
    _database_path if _database_path.is_absolute() else BASE_DIR / _database_path
)


@dataclass(frozen=True)
class Settings:
    app_name: str = APP_NAME
    app_full_name: str = APP_FULL_NAME
    gemini_api_key: str | None = GEMINI_API_KEY
    gemini_model: str = GEMINI_MODEL
    database_path: Path = DATABASE_PATH
    operator_password: str | None = AEGIS_OPERATOR_PASSWORD


settings = Settings()
