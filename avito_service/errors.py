"""Public-safe, machine-readable errors for the isolated Avito service."""

from __future__ import annotations

from typing import Any, Mapping


class AvitoServiceError(RuntimeError):
    """Base error whose message and metadata are safe for the local client."""

    default_code = "SERVICE_UNAVAILABLE"
    default_retryable = True

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.retryable = self.default_retryable if retryable is None else retryable
        # Internal diagnostics are deliberately excluded from public_dict().
        # Callers may attach only already-sanitized provider metadata here.
        self.diagnostics = dict(diagnostics or {})

    def public_dict(self) -> dict[str, object]:
        return {
            "error": str(self),
            "code": self.code,
            "retryable": self.retryable,
        }


class ConfigurationError(AvitoServiceError):
    """Required local configuration is missing or invalid."""

    default_code = "CONFIGURATION_REQUIRED"
    default_retryable = False


class ExternalServiceError(AvitoServiceError):
    """Apify or the AI provider failed without exposing credentials."""

    default_code = "EXTERNAL_SERVICE_UNAVAILABLE"
    default_retryable = True


class ApifyPlanRequiredError(ExternalServiceError):
    """The configured Apify account cannot run the selected Actor plan."""

    default_code = "APIFY_PLAN_REQUIRED"
    default_retryable = False


class InvalidDatasetError(AvitoServiceError):
    """Uploaded Zen data does not have the expected JSON shape."""

    default_code = "INVALID_DATASET"
    default_retryable = False


class SearchCancelledError(AvitoServiceError):
    """A user stopped an in-process search; partial results are never public."""

    default_code = "SEARCH_CANCELLED"
    default_retryable = False

    def __init__(self, message: str = "Поиск остановлен.") -> None:
        super().__init__(message, code=self.default_code, retryable=False)
