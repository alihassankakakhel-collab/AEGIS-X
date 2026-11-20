from config import settings


def test_settings_have_defaults():
    assert settings.app_name
    assert settings.gemini_model
    assert settings.database_path.name.endswith(".db")
