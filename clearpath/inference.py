"""Provider protocol and narrow OpenAI-compatible chat adapter (S2).

Separates the DeepSeek coding agent (this board's worker) from the app's
runtime evaluator. The adapter owns base URL, model id, key, timeout, and
token limit — all read from the server environment, never from the browser
or from user-controlled input. This module makes no workflow decisions and
touches no SQL; it only sends bounded chat requests and returns the raw
response content plus attribution metadata.

Failure states are explicit (NOT_CONFIGURED / TIMEOUT / CONNECTION /
HTTP / INVALID_RESPONSE / EMPTY_RESPONSE) so downstream callers can never
confuse a provider failure with a clean result.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# --- Canonical provider failure states ---------------------------------------
NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
TIMEOUT = "PROVIDER_TIMEOUT"
CONNECTION_ERROR = "PROVIDER_CONNECTION_ERROR"
HTTP_ERROR = "PROVIDER_HTTP_ERROR"
INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
EMPTY_RESPONSE = "PROVIDER_EMPTY_RESPONSE"

# Environment keys (server-side only).
ENV_BASE_URL = "CLEARPATH_INFERENCE_BASE_URL"
ENV_MODEL_ID = "CLEARPATH_INFERENCE_MODEL"
ENV_API_KEY = "CLEARPATH_INFERENCE_API_KEY"
ENV_TIMEOUT_S = "CLEARPATH_INFERENCE_TIMEOUT_S"
ENV_MAX_TOKENS = "CLEARPATH_INFERENCE_MAX_TOKENS"
ENV_MAX_RESPONSE_CHARS = "CLEARPATH_INFERENCE_MAX_RESPONSE_CHARS"

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_MAX_TOKENS = 1024
DEFAULT_MAX_RESPONSE_CHARS = 8_000


class InferenceError(Exception):
    """Domain error carrying an explicit failure state."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class ProviderConfig:
    """Server-side provider configuration. Never expose api_key."""

    base_url: str
    model_id: str
    api_key: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS


@dataclass
class ProviderResponse:
    """Attributed result from one chat call. provider_revision is the
    model id / fingerprint the provider returned, never a secret."""

    content: str
    latency_ms: float
    token_usage: dict | None = None
    provider_revision: str | None = None


def fingerprint(config: ProviderConfig) -> str:
    """Config fingerprint for run identity. Never includes the API key."""
    payload = {
        "base_url": config.base_url,
        "model_id": config.model_id,
        "timeout_s": config.timeout_s,
        "max_tokens": config.max_tokens,
        "max_response_chars": config.max_response_chars,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def config_from_env(environ: dict[str, str] | None = None) -> ProviderConfig:
    """Read provider config from server environment with safe defaults."""
    env = environ if environ is not None else os.environ
    base_url = env.get(ENV_BASE_URL, DEFAULT_BASE_URL)
    model_id = env.get(ENV_MODEL_ID, DEFAULT_MODEL_ID)
    api_key = env.get(ENV_API_KEY) or None
    timeout_s = float(env.get(ENV_TIMEOUT_S, DEFAULT_TIMEOUT_S))
    max_tokens = int(env.get(ENV_MAX_TOKENS, DEFAULT_MAX_TOKENS))
    max_chars = int(env.get(ENV_MAX_RESPONSE_CHARS, DEFAULT_MAX_RESPONSE_CHARS))
    return ProviderConfig(
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
        max_response_chars=max_chars,
    )


class ChatProvider(Protocol):
    """Minimal chat contract implemented by adapters."""

    def chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None) -> ProviderResponse: ...

    @property
    def config(self) -> ProviderConfig: ...


class OpenAICompatAdapter:
    """Narrow OpenAI-compatible chat adapter using httpx.

    Sends a single chat completion request with bounded max_tokens, maps
    failures to explicit InferenceError states, and records latency/token
    usage/provider revision for attribution. No tools, no retries, no
    browser-visible keys.
    """

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_s,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=1),
        )

    def close(self) -> None:
        self._client.close()

    def chat(self, messages: list[dict[str, Any]], max_tokens: int | None = None) -> ProviderResponse:
        if not self.config.api_key:
            raise InferenceError(
                NOT_CONFIGURED,
                "No inference API key configured; set CLEARPATH_INFERENCE_API_KEY.",
            )

        payload: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": messages,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}

        start = time.monotonic()
        try:
            response = self._client.post("/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise InferenceError(
                TIMEOUT,
                "Provider request timed out",
                {"timeout_s": self.config.timeout_s},
            ) from exc
        except httpx.HTTPError as exc:
            raise InferenceError(
                CONNECTION_ERROR,
                "Provider connection failed",
                {"error": str(exc)},
            ) from exc
        latency_ms = (time.monotonic() - start) * 1000.0

        if response.status_code != 200:
            raise InferenceError(
                HTTP_ERROR,
                f"Provider returned HTTP {response.status_code}",
                {"status_code": response.status_code, "latency_ms": latency_ms},
            )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise InferenceError(
                INVALID_RESPONSE,
                "Provider returned non-JSON body",
                {"latency_ms": latency_ms},
            ) from exc

        content: Any = None
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            content = message.get("content")

        if content is None:
            raise InferenceError(
                EMPTY_RESPONSE,
                "Provider returned no assistant content",
                {"latency_ms": latency_ms, "finish_reason": choices[0].get("finish_reason") if choices else None},
            )

        usage = data.get("usage")
        revision = data.get("model")
        return ProviderResponse(
            content=str(content),
            latency_ms=latency_ms,
            token_usage=usage if isinstance(usage, dict) else None,
            provider_revision=str(revision) if revision else None,
        )


def build_adapter(config: ProviderConfig | None = None) -> OpenAICompatAdapter:
    """Convenience factory for downstream modules (S3+)."""
    return OpenAICompatAdapter(config or config_from_env())
