"""Application settings, read from the environment and the repo-root `.env` (development only).

The app refuses to start when a required secret is missing, too short, or still a placeholder
(blueprint §5.2). Dev-only defaults are rejected outside development/test, so each environment
must bring its own credentials (§5.1).
"""

import ipaddress
import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MIN_SECRET_LENGTH = 32
PLACEHOLDER_VALUES = frozenset(
    {
        "",
        "replace-locally",
        "change-me",
        "changeme",
        "your-provider",
        "your-model",
        "your-dimension",
        "secret",
    }
)
DEV_ONLY_MARKER = "dev-only"  # compose.yaml's local Postgres password contains this
TLS_SSLMODE = re.compile(r"[?&]sslmode=(require|verify-ca|verify-full)(&|$)")
KEYGEN_COMMAND = 'python -c "import secrets; print(secrets.token_urlsafe(48))"'


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class MalwareScanner(StrEnum):
    BUILTIN = "builtin"  # executable signatures + the EICAR test string; development and test only
    CLAMD = "clamd"  # ClamAV daemon over TCP; required for a public deployment (§7.1)


class ModelProvider(StrEnum):
    OPENAI = "openai"
    LOCAL = "local"  # an OpenAI-compatible server we run (llama.cpp, models in LLM/; ADR-026): no key or cost
    FAKE = "fake"  # deterministic local substitute for development and tests (§5.1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    tenant_timezone: str = "America/Chicago"

    # The API and worker connect as a non-owner role so row-level security applies (ADR-011).
    database_url: PostgresDsn
    # Schema owner, used only by Alembic. Defaults to DATABASE_URL when unset.
    migration_database_url: PostgresDsn | None = None
    # §11.3 timeouts: a stuck query or an unreachable database fails the request instead of hanging it.
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100, le=600_000)
    # Shown in pg_stat_activity and Postgres logs, so each service's connections are attributable (§16).
    db_application_name: str = Field(default="knowledgeai", pattern=r"^[a-z0-9-]{1,63}$")
    chroma_host: str = "127.0.0.1"  # not "localhost": Windows tries IPv6 ::1 first, adding ~2 s per connect
    chroma_port: int = Field(default=8001, ge=1, le=65535)
    chroma_ssl: bool = False  # required outside development (§11.2 TLS)
    # Changing the embedding model or dimension means a new collection plus a rebuild (docs/data-model.md §5).
    chroma_collection: str = Field(default="knowledge_chunks", pattern=r"^[a-z0-9][a-z0-9_-]{2,62}$")

    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_local_dir: Path = REPO_ROOT / "data" / "uploads"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None  # unset for AWS; set for S3-compatible stores
    s3_region: str = "us-east-1"
    # Unset on a cloud host: boto3's default chain (instance/task role, environment) supplies credentials.
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_connect_timeout_seconds: float = Field(default=5.0, gt=0)  # §11.3 timeouts on object storage
    s3_read_timeout_seconds: float = Field(default=30.0, gt=0)
    s3_max_attempts: int = Field(default=3, ge=1, le=10)  # including the first try

    embedding_provider: ModelProvider
    embedding_model: str
    embedding_dimension: int = Field(gt=0)
    embedding_batch_size: int = Field(default=64, ge=1, le=2048)
    # `local` only: where the embedding server listens, and the task prefixes its model expects (nomic-embed:
    # "search_query: " for questions, "search_document: " for chunks).
    embedding_base_url: str | None = None
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    embedding_price_per_1k_tokens: float = Field(default=0.0, ge=0)  # for spend logging (§7.3)
    llm_provider: ModelProvider
    llm_model: str
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None  # `local` only: the llama.cpp server's OpenAI-compatible /v1 URL
    # §13.1 estimated cost per question: list prices in USD (0 for the fake providers).
    llm_price_per_1k_input_tokens: float = Field(default=0.0, ge=0)
    llm_price_per_1k_output_tokens: float = Field(default=0.0, ge=0)
    # §16 cost budget: alert "model_budget" when 24 h of estimated spend reaches it (unset: no rule).
    llm_daily_budget_usd: float | None = Field(default=None, gt=0)

    # Upload limits (§7.1, all configurable)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=300, gt=0)
    max_active_ingestions_per_org: int = Field(default=5, gt=0)
    # Chunking (§7.3: start at 350-700 tokens with 50-100 overlap, then benchmark)
    chunk_target_tokens: int = Field(default=500, ge=100)
    chunk_max_tokens: int = Field(default=700, ge=100)
    chunk_overlap_tokens: int = Field(default=75, ge=0)
    malware_scanner: MalwareScanner = MalwareScanner.BUILTIN
    clamd_host: str = "127.0.0.1"
    clamd_port: int = Field(default=3310, ge=1, le=65535)

    # Ingestion worker (§7.4, ADR-008)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    worker_lease_seconds: int = Field(default=600, ge=30)  # a job whose lease expires is requeued
    worker_retry_base_seconds: int = Field(default=30, ge=1)  # backoff: base * 2^(attempt-1), capped
    worker_retry_max_seconds: int = Field(default=900, ge=1)
    reconcile_interval_seconds: int = Field(default=900, ge=60)
    orphan_grace_seconds: int = Field(default=3600, ge=60)  # never delete files younger than this
    # §11.3 health: the worker's heartbeat and the queue's oldest wait (GET /health)
    worker_heartbeat_seconds: float = Field(default=10.0, gt=0)
    worker_stale_seconds: int = Field(default=60, ge=10)  # no heartbeat for this long: the worker is down
    queue_max_wait_seconds: int = Field(
        default=900, ge=60
    )  # a claimable job waiting longer: the queue is stuck

    # §11.2 retention (ADR-019; the periods for real customer data are a §19 decision)
    conversation_retention_days: int = Field(default=180, ge=1)  # by last activity
    deleted_document_purge_days: int = Field(default=30, ge=0)  # soft-deleted documents: then data is purged
    metrics_retention_days: int = Field(default=90, ge=1)  # request metrics and resolved alerts (§13.2)

    # §13 metrics and alerts (ADR-021)
    metrics_flush_seconds: float = Field(default=15.0, gt=0)  # how often each API process writes its counters
    alert_ingestion_failures: int = Field(default=5, ge=1)  # failed ingestion jobs in ALERT_WINDOW_MINUTES
    alert_error_rate: float = Field(default=0.05, gt=0, le=1)  # share of 5xx responses …
    alert_min_requests: int = Field(default=20, ge=1)  # … once at least this many requests were seen
    alert_window_minutes: int = Field(default=15, ge=1, le=1440)
    alert_check_seconds: float = Field(default=60.0, gt=0)  # how often the worker evaluates the rules
    alert_webhook_url: SecretStr | None = None  # Slack-compatible webhook; alerts are always logged too

    # Question answering (§8, budgets in docs/architecture.md §3)
    max_question_chars: int = Field(default=1000, ge=50, le=8000)
    retrieval_candidates: int = Field(default=10, ge=1, le=50)  # fetched from Chroma
    # §8.5 hybrid retrieval (ADR-028): keyword candidates from Postgres full-text search, fused with the
    # vector candidates by reciprocal rank (RRF_K is the usual 60).
    keyword_search: bool = True
    rrf_k: int = Field(default=60, ge=1, le=1000)
    # Optional reranker (ADR-028): the local bge-reranker-v2-m3 server orders the candidates. Its scores
    # don't separate answerable from unanswerable questions well enough to replace the evidence check
    # (docs/demos/p1-retrieval.md), so they only order sources unless RERANK_RELAXED_SCORE is set.
    reranker_provider: Literal["none", "local"] = "none"
    reranker_base_url: str | None = None  # e.g. http://reranker:8080/v1
    reranker_model: str = "bge-reranker-v2-m3-q8_0"
    reranker_timeout_seconds: float = Field(default=10.0, gt=0)
    # A source scoring at least this passes with RERANK_RELAXED_COVERAGE of the question's words (unset: off).
    rerank_relaxed_score: float | None = None
    rerank_relaxed_coverage: float = Field(default=0.4, gt=0, le=1)
    context_chunks: int = Field(default=5, ge=1, le=20)  # sent to the LLM
    context_token_budget: int = Field(default=6000, ge=500)
    min_evidence_similarity: float = Field(default=0.05, ge=0, le=1)  # cosine similarity floor
    min_term_coverage: float = Field(default=0.6, gt=0, le=1)  # share of question terms a source must contain
    answer_max_tokens: int = Field(default=500, ge=50, le=4000)
    llm_timeout_seconds: float = Field(default=20.0, gt=0)
    request_deadline_seconds: float = Field(default=30.0, gt=0)
    # Orders (US-05, ADR-029): the HMAC key for customer contact hashes (unset: derived from AUTH_JWT_SECRET,
    # development only) and the lockout after failed verifications, per caller and per order number.
    customer_lookup_secret: SecretStr | None = None
    order_lookup_max_failures: int = Field(default=5, ge=1)
    order_lookup_window_seconds: int = Field(default=900, ge=60)
    # How long a guest's verified order or repair stays usable for follow-up questions in that chat (ADR-031).
    account_grant_minutes: int = Field(default=30, ge=1, le=24 * 60)
    rate_limit_anonymous_per_minute: int = Field(default=10, ge=1)
    rate_limit_user_per_minute: int = Field(default=30, ge=1)
    # All anonymous sessions together: a client that drops its cookie for a new session still hits this cap.
    rate_limit_public_per_minute: int = Field(default=300, ge=1)
    login_max_failures: int = Field(default=5, ge=1)  # per account, then locked for LOGIN_LOCKOUT_SECONDS
    login_lockout_seconds: int = Field(default=900, ge=60)
    # Single-tenant deployments: the organization behind the public chat and the voice service credential.
    public_organization: str = Field(default="techcare", pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    internal_service_organization: str = Field(default="techcare", pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")

    auth_jwt_secret: SecretStr
    auth_token_minutes: int = Field(default=60, ge=5, le=24 * 60)
    preview_link_seconds: int = Field(default=300, ge=30, le=3600)  # signed file links (§11.1)
    internal_service_token: SecretStr
    # §11.1: where the voice service may call from (comma-separated CIDRs; the TCP peer, not X-Forwarded-For).
    internal_api_allowed_networks: str = "127.0.0.0/8,::1/128"

    @field_validator("internal_api_allowed_networks")
    @classmethod
    def _networks(cls, value: str) -> str:
        for part in value.split(","):
            ipaddress.ip_network(part.strip(), strict=False)  # ValueError → startup refuses the setting
        return value

    @property
    def internal_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(p.strip(), strict=False)
            for p in self.internal_api_allowed_networks.split(",")
        )

    @property
    def deployed(self) -> bool:
        return self.app_env in (AppEnv.STAGING, AppEnv.PRODUCTION)

    @field_validator("tenant_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r} (use e.g. 'America/Chicago')") from exc
        return value

    @field_validator("embedding_model", "llm_model")
    @classmethod
    def _not_placeholder(cls, value: str) -> str:
        if value.strip().lower() in PLACEHOLDER_VALUES:
            raise ValueError("still a placeholder; set a real value in .env")
        return value

    @field_validator("auth_jwt_secret", "internal_service_token")
    @classmethod
    def _strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw.strip().lower() in PLACEHOLDER_VALUES:
            raise ValueError(f"still a placeholder; generate one with: {KEYGEN_COMMAND}")
        if len(raw) < MIN_SECRET_LENGTH:
            raise ValueError(f"must be at least {MIN_SECRET_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _environment_rules(self) -> Self:
        deployed = self.app_env in (AppEnv.STAGING, AppEnv.PRODUCTION)

        if not self.chunk_overlap_tokens < self.chunk_target_tokens <= self.chunk_max_tokens:
            raise ValueError("chunking needs CHUNK_OVERLAP_TOKENS < CHUNK_TARGET_TOKENS <= CHUNK_MAX_TOKENS")

        if self.auth_jwt_secret.get_secret_value() == self.internal_service_token.get_secret_value():
            raise ValueError("AUTH_JWT_SECRET and INTERNAL_SERVICE_TOKEN must be different secrets")

        for name in ("llm_provider", "embedding_provider"):
            if getattr(self, name) is ModelProvider.FAKE and deployed:
                raise ValueError(f"{name.upper()}=fake is only allowed in development and test")
        if self.llm_provider is ModelProvider.LOCAL and not self.llm_base_url:
            raise ValueError(
                "LLM_PROVIDER=local needs LLM_BASE_URL (the llama.cpp server, e.g. http://llm:8080/v1)"
            )
        if self.embedding_provider is ModelProvider.LOCAL and not self.embedding_base_url:
            raise ValueError(
                "EMBEDDING_PROVIDER=local needs EMBEDDING_BASE_URL (e.g. http://embedder:8080/v1)"
            )
        if self.reranker_provider == "local" and not self.reranker_base_url:
            raise ValueError("RERANKER_PROVIDER=local needs RERANKER_BASE_URL (e.g. http://reranker:8080/v1)")
        uses_real_provider = ModelProvider.OPENAI in (self.llm_provider, self.embedding_provider)
        if uses_real_provider:
            key = self.llm_api_key.get_secret_value() if self.llm_api_key else ""
            if key.strip().lower() in PLACEHOLDER_VALUES:
                raise ValueError("LLM_API_KEY is required when a real model provider is configured")

        if deployed:
            for name in ("database_url", "migration_database_url"):
                if DEV_ONLY_MARKER in str(getattr(self, name) or ""):
                    raise ValueError(
                        f"{name.upper()} uses the local dev-only credentials; use this environment's own"
                    )
            if self.malware_scanner is not MalwareScanner.CLAMD:
                raise ValueError("staging/production require MALWARE_SCANNER=clamd")
            if self.storage_backend is not StorageBackend.S3 or not self.s3_bucket:
                raise ValueError("staging/production require STORAGE_BACKEND=s3 and S3_BUCKET")
            # §11.2: TLS on every hop outside development.
            if not TLS_SSLMODE.search(str(self.database_url)):
                raise ValueError(
                    "staging/production require DATABASE_URL with sslmode=require or verify-full"
                )
            if not self.chroma_ssl:
                raise ValueError("staging/production require CHROMA_SSL=true")
            if self.s3_endpoint_url and not self.s3_endpoint_url.startswith("https://"):
                raise ValueError("S3_ENDPOINT_URL must use https:// outside development")
            # ADR-029: its own key, so rotating the session secret never breaks order verification.
            lookup = self.customer_lookup_secret.get_secret_value() if self.customer_lookup_secret else ""
            if len(lookup) < 32 or lookup.strip().lower() in PLACEHOLDER_VALUES:
                raise ValueError("staging/production require CUSTOMER_LOOKUP_SECRET (32+ characters)")
        return self

    @property
    def migration_url(self) -> str:
        return str(self.migration_database_url or self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
