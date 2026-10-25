"""Application error hierarchy.

Services raise these; the API layer maps them to HTTP responses in one place
so the transport never leaks into business code.
"""

from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "Something went wrong.", *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class AuthRequiredError(AppError):
    """No guest id and no session token came with the request."""

    status_code = 401
    code = "auth_required"


class SessionExpiredError(AppError):
    """The bearer token is unknown or past its expiry; the client should drop it."""

    status_code = 401
    code = "session_expired"


class ConfigurationError(AppError):
    """The server is missing a setting it cannot run without."""

    status_code = 500
    code = "misconfigured"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class UnsupportedSourceError(AppError):
    status_code = 422
    code = "unsupported_source"


class SourceNotReadyError(AppError):
    status_code = 409
    code = "source_not_ready"


class IngestionError(AppError):
    """Raised while loading or processing a source; stored on the Source row."""

    status_code = 502
    code = "ingestion_failed"


class StaleIndexError(AppError):
    """The stored index was built by a different embedding model."""

    status_code = 409
    code = "stale_index"


class EmbeddingUnavailableError(AppError):
    status_code = 503
    code = "embedding_unavailable"


class NoModelAvailableError(AppError):
    status_code = 503
    code = "no_model_available"
