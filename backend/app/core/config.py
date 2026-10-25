"""Application settings.

Everything tunable lives here and is read from environment variables or a
`.env` file in the backend directory. Comma-separated strings are used for
list-valued settings so they stay easy to set in shells and Docker.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- server ----
    app_name: str = "LinkChat API"
    app_version: str = "1.0.0"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    # ---- paths ----
    # Local disk holds only what is transient or re-downloadable: the embedding
    # model cache and uploaded files while they are being ingested. Everything
    # a user would miss lives in Postgres.
    data_dir: Path = BACKEND_DIR / "data"
    models_config_path: Path = BACKEND_DIR / "config" / "models.yaml"
    schema_path: Path = BACKEND_DIR / "db" / "schema.postgres.sql"

    # ---- database ----
    # Postgres with the pgvector extension (Neon's free tier works). The
    # pooled connection string is the right one for a web server.
    database_url: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 5
    # Neon suspends idle databases; dropping idle connections sooner than that
    # avoids handing a dead socket to the next request.
    db_pool_max_idle_seconds: float = 240.0

    # ---- identity ----
    # Guests are a browser-generated id; their data lives this long after their
    # last request. Users sign in with an emailed code; a session lasts this long
    # and is extended on use.
    auth_secret: str = ""  # HMAC key for hashing codes and tokens; required in production
    guest_ttl_hours: int = 24
    session_ttl_hours: int = 24 * 7
    login_code_ttl_minutes: int = 10
    login_code_max_attempts: int = 5
    cleanup_interval_minutes: int = 60

    # ---- email (login codes) ----
    # console: print the code to the server log (development).
    # resend:  send through https://resend.com (free tier, needs RESEND_API_KEY).
    email_provider: str = "console"
    resend_api_key: str = ""
    email_from: str = "LinkChat <onboarding@resend.dev>"

    # ---- ingestion ----
    fetch_timeout_seconds: float = 25.0
    max_upload_bytes: int = 40 * 1024 * 1024
    max_audio_bytes: int = 24 * 1024 * 1024  # hosted Whisper endpoints cap around 25 MB
    max_document_chars: int = 2_000_000
    min_document_chars: int = 120
    chunk_size: int = Field(default=1000, ge=200)
    chunk_overlap: int = Field(default=150, ge=0)
    youtube_languages: str = "en,en-US,en-GB"
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LinkChat/1.0"
    )

    # ---- embeddings ----
    # Tried in order; the first that loads wins. Measured 2026-10-17 on
    # cross-language retrieval (English documents, questions in hi/es/fr/en) and
    # on English-only retrieval:
    #   mpnet-base-v2    multilingual 5/5, English top1 7/8   768 dim, 1.0 GB
    #   MiniLM-L12-v2    multilingual 5/5, English top1 6/8   384 dim, 0.22 GB
    #   bge-small-en     multilingual 3/5, English top1 6/8   384 dim, 0.07 GB
    # The multilingual pair wins outright, so they lead; the English-only models
    # remain as light fallbacks if the downloads are unavailable.
    embedding_models: str = (
        "sentence-transformers/paraphrase-multilingual-mpnet-base-v2,"
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2,"
        "BAAI/bge-small-en-v1.5,"
        "sentence-transformers/all-MiniLM-L6-v2,"
        "snowflake/snowflake-arctic-embed-s,"
        "BAAI/bge-base-en-v1.5"
    )
    embedding_batch_size: int = 32

    # ---- retrieval ----
    retrieval_top_k: int = Field(default=6, ge=1, le=20)
    vector_candidates: int = 12
    keyword_candidates: int = 12
    rrf_k: int = 60

    # ---- javascript rendering fallback ----
    # Needs `pip install -r requirements-optional.txt` and `playwright install chromium`.
    browser_fallback_enabled: bool = False
    browser_timeout_seconds: float = 25.0
    browser_wait_until: str = "networkidle"
    browser_min_chars: int = 600  # below this, a rendered retry is worth the cost

    # ---- preset modes over a whole document ----
    # Sized for a free tier: Groq allows 8000 tokens per minute and rejects any
    # single request above 7000 input tokens. Windows stay well under both, and
    # the pacer spreads requests across the minute.
    mode_max_input_tokens: int = 2500
    mode_prompt_overhead_tokens: int = 700
    mode_map_max_tokens: int = 2000  # reasoning models spend most of this thinking
    mode_max_rounds: int = 3  # condensing rounds before the final answer
    mode_reduce_max_tokens: int = 1600
    mode_max_windows: int = 24
    mode_tokens_per_minute: int = 8000  # 0 disables pacing
    mode_max_wait_seconds: float = 90.0

    # ---- concurrency and rate limits ----
    # Each in-flight ingestion costs roughly 32 MB and 16 seconds of CPU for a
    # long article, and submissions are otherwise unbounded, so a burst of them
    # is what exhausts a small instance. Queue beyond this instead.
    max_concurrent_ingestions: int = 4
    # Per client address, per minute. 0 disables a bucket.
    rate_limit_chat_per_minute: int = 20
    rate_limit_ingest_per_minute: int = 10
    rate_limit_general_per_minute: int = 120
    # Sending a login code costs an email, so this bucket is tight.
    rate_limit_auth_per_minute: int = 5

    # ---- collections ----
    collection_per_source_k: int = 4
    collection_max_sources: int = 20

    # ---- model pool validation ----
    validate_model_pool: bool = True
    model_listing_timeout_seconds: float = 10.0

    # ---- chat ----
    max_history_messages: int = 8
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    llm_request_timeout_seconds: float = 60.0
    citation_preview_chars: int = 420

    # ---- circuit breaker ----
    breaker_failure_threshold: int = 2
    breaker_base_cooldown_seconds: float = 60.0
    breaker_max_cooldown_seconds: float = 900.0
    breaker_hard_failure_cooldown_seconds: float = 3600.0

    @property
    def cors_origin_list(self) -> list[str]:
        return _split_csv(self.cors_origins)

    @property
    def youtube_language_list(self) -> list[str]:
        return _split_csv(self.youtube_languages)

    @property
    def embedding_model_list(self) -> list[str]:
        return _split_csv(self.embedding_models)

    @property
    def embedding_cache_dir(self) -> Path:
        return self.data_dir / "embedding-models"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


def load_env_file(path: Path = BACKEND_DIR / ".env") -> None:
    """Export `.env` into the process environment (without overriding real env vars).

    Settings fields are read by pydantic-settings directly, but provider API keys
    are looked up from `os.environ` by the model registry, so the file has to be
    exported as well.
    """
    from dotenv import load_dotenv

    if path.exists():
        load_dotenv(path, override=False)


@lru_cache
def get_settings() -> Settings:
    load_env_file()
    return Settings()
