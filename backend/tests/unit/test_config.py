"""Blueprint §5.2: refuse to start on missing/placeholder secrets; §5.1: per-environment credentials."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.config import AppEnv, ModelProvider, Settings

STRONG_A = "a" * 40
STRONG_B = "b" * 40
ENV_KEYS = [
    "APP_ENV",
    "DATABASE_URL",
    "CHROMA_HOST",
    "CHROMA_PORT",
    "STORAGE_BACKEND",
    "STORAGE_LOCAL_DIR",
    "S3_BUCKET",
    "MALWARE_SCANNER",
    "CHUNK_TARGET_TOKENS",
    "CHUNK_MAX_TOKENS",
    "CHUNK_OVERLAP_TOKENS",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "AUTH_JWT_SECRET",
    "INTERNAL_SERVICE_TOKEN",
    "TENANT_TIMEZONE",
]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def valid(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "database_url": "postgresql+psycopg://knowledgeai:knowledgeai-dev-only@localhost:5432/knowledgeai",
        "embedding_provider": "fake",
        "embedding_model": "fake-embedding",
        "embedding_dimension": 384,
        "llm_provider": "fake",
        "llm_model": "fake-llm",
        "auth_jwt_secret": STRONG_A,
        "internal_service_token": STRONG_B,
    }
    values.update(overrides)
    return values


def make(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **valid(**overrides))


def test_valid_development_settings_load() -> None:
    settings = make()
    assert settings.app_env is AppEnv.DEVELOPMENT
    assert settings.llm_provider is ModelProvider.FAKE


@pytest.mark.parametrize(
    "missing", ["database_url", "auth_jwt_secret", "internal_service_token", "llm_model"]
)
def test_missing_required_value_refuses_to_start(missing: str) -> None:
    values = valid()
    del values[missing]
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **values)


@pytest.mark.parametrize("secret", ["replace-locally", "short-secret"])
def test_placeholder_or_short_secret_is_rejected(secret: str) -> None:
    with pytest.raises(ValidationError, match="auth_jwt_secret"):
        make(auth_jwt_secret=secret)


def test_secrets_must_differ() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        make(internal_service_token=STRONG_A)


def test_placeholder_model_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make(llm_model="your-model")


def test_real_provider_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        make(llm_provider="openai", llm_model="some-model")
    assert make(llm_provider="openai", llm_model="some-model", llm_api_key="sk-test-" + "x" * 20)


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="IANA timezone"):
        make(tenant_timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize("env", ["staging", "production"])
class TestDeployedEnvironments:
    def deployed(self, env: str, **overrides: Any) -> Settings:
        base: dict[str, Any] = {
            "app_env": env,
            "database_url": f"postgresql+psycopg://app:{STRONG_A}@db.internal:5432/knowledgeai?sslmode=verify-full",
            "chroma_ssl": True,
            "storage_backend": "s3",
            "s3_bucket": f"knowledgeai-{env}",
            "malware_scanner": "clamd",
            "llm_provider": "openai",
            "embedding_provider": "openai",
            "llm_model": "some-model",
            "embedding_model": "some-embedding",
            "llm_api_key": "sk-live-" + "x" * 20,
            "customer_lookup_secret": "c" * 40,
        }
        base.update(overrides)
        return make(**base)

    def test_proper_deployed_settings_load(self, env: str) -> None:
        assert self.deployed(env).app_env.value == env

    def test_order_verification_needs_its_own_secret(self, env: str) -> None:
        # ADR-029: rotating the session secret must never break order verification, so it has its own key.
        for secret in (None, "short", "changeme"):
            with pytest.raises(ValidationError, match="CUSTOMER_LOOKUP_SECRET"):
                self.deployed(env, customer_lookup_secret=secret)

    def test_local_models_need_no_key_and_are_accepted(self, env: str) -> None:
        """ADR-026: our own llama.cpp servers; no API key, no spend."""
        settings = self.deployed(
            env,
            llm_provider="local",
            embedding_provider="local",
            llm_api_key=None,
            llm_base_url="http://llm:8080/v1",
            embedding_base_url="http://embedder:8080/v1",
        )
        assert settings.llm_provider is ModelProvider.LOCAL and settings.llm_api_key is None

    def test_fake_models_are_refused(self, env: str) -> None:
        with pytest.raises(ValidationError, match="only allowed in development"):
            self.deployed(env, llm_provider="fake")

    def test_dev_only_database_credentials_are_refused(self, env: str) -> None:
        with pytest.raises(ValidationError, match="dev-only"):
            self.deployed(env, database_url="postgresql+psycopg://knowledgeai:knowledgeai-dev-only@db:5432/k")

    def test_local_storage_is_refused(self, env: str) -> None:
        with pytest.raises(ValidationError, match="STORAGE_BACKEND=s3"):
            self.deployed(env, storage_backend="local")

    def test_builtin_malware_scanner_is_refused(self, env: str) -> None:
        with pytest.raises(ValidationError, match="MALWARE_SCANNER=clamd"):
            self.deployed(env, malware_scanner="builtin")

    # §11.2: TLS on every hop outside development.
    @pytest.mark.parametrize(
        "query", ["", "?sslmode=disable", "?sslmode=prefer", "?application_name=x&sslmode=allow"]
    )
    def test_database_without_tls_is_refused(self, env: str, query: str) -> None:
        with pytest.raises(ValidationError, match="sslmode=require"):
            self.deployed(env, database_url=f"postgresql+psycopg://app:{STRONG_A}@db.internal:5432/k{query}")

    def test_require_and_verify_modes_are_accepted(self, env: str) -> None:
        for mode in ("require", "verify-ca", "verify-full"):
            url = f"postgresql+psycopg://app:{STRONG_A}@db.internal:5432/k?connect_timeout=5&sslmode={mode}"
            assert self.deployed(env, database_url=url).deployed

    def test_plain_http_to_chroma_or_s3_is_refused(self, env: str) -> None:
        with pytest.raises(ValidationError, match="CHROMA_SSL=true"):
            self.deployed(env, chroma_ssl=False)
        with pytest.raises(ValidationError, match="https://"):
            self.deployed(env, s3_endpoint_url="http://s3.example.com")


def test_internal_networks_are_parsed_and_validated() -> None:
    settings = make(internal_api_allowed_networks="10.20.0.0/16, ::1/128")
    assert [str(n) for n in settings.internal_networks] == ["10.20.0.0/16", "::1/128"]
    with pytest.raises(ValidationError):
        make(internal_api_allowed_networks="10.20.0.0/16,not-a-network")


@pytest.mark.parametrize(("target", "maximum", "overlap"), [(500, 400, 75), (500, 700, 500), (300, 700, 400)])
def test_inconsistent_chunk_sizes_are_rejected(target: int, maximum: int, overlap: int) -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS < CHUNK_TARGET_TOKENS"):
        make(chunk_target_tokens=target, chunk_max_tokens=maximum, chunk_overlap_tokens=overlap)


@pytest.mark.parametrize(
    ("provider", "missing"), [("llm_provider", "LLM_BASE_URL"), ("embedding_provider", "EMBEDDING_BASE_URL")]
)
def test_local_models_need_their_server_url(provider: str, missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        make(**{provider: "local"})
