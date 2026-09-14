from app.core.config import Settings

REQUIRED_ENV = {
    "MARITALK_API_KEY": "chave-de-teste",
    "MARITALK_API_BASE": "https://exemplo.invalido",
    "MARITALK_MODEL": "modelo-de-teste",
    "ADMIN_API_TOKEN": "token-de-teste",
}


def test_allowed_origins_parses_a_comma_separated_env_value(monkeypatch):
    """Regression: pydantic-settings JSON-decodes complex-typed fields sourced
    from the environment before any validator runs, so a naturally-written
    comma-separated ALLOWED_ORIGINS would raise at startup if the field were
    typed list[str]. Settings() must accept the real env-sourced path, not
    just direct-kwarg construction in tests.
    """
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example, https://b.example")

    settings = Settings(_env_file=None)

    assert settings.allowed_origins_list() == ["https://a.example", "https://b.example"]


def test_allowed_origins_falls_back_to_the_documented_dev_default(monkeypatch):
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.allowed_origins_list() == [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ]
