"""Parse and validate environment settings exactly once, then freeze them for the boot."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Invalid startup configuration (messages must never include secret values)."""


def origin(value: str) -> str:
    """Return a URL's non-secret origin, omitting credentials, path and query."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"
    except ValueError:
        raise ConfigurationError("Invalid HTTP origin configuration") from None


@dataclass(frozen=True)
class Config:
    aiab_mode: str = "local"
    aiab_id: str = "aiab-dev"
    enforcement_mode: str = "monitor"
    block_threshold: float = 0.8
    inference_url: str = "http://inference:8080"
    listen_port: int = 8443
    cors_allow_origin: str | None = None
    sample_rate: float = 0.05
    report_period_seconds: int = 600
    report_history: int = 100
    tinfoil_api_key: str | None = field(default=None, repr=False)
    classifier_model: str = "mock-keywords/v1"
    classifier_definition: str = "classifiers/ai_capabilities.md"
    definition_text: str = field(default="", repr=False)
    log_level: str = "INFO"
    # Reviewed constants, deliberately not configurable through the environment.
    processing_timeout_seconds: float = 30.0
    classifier_attempt_timeout_seconds: float = 10.0
    upstream_timeout_seconds: float = 15.0
    classifier_attempts: int = 2
    monitor_queue_limit: int = 64
    monitor_workers: int = 2
    classifier_concurrency: int = 4
    max_request_bytes: int = 1_048_576

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        env = os.environ if environ is None else environ
        if "CLASSIFIER_ENDPOINT_OVERRIDE" in env:
            raise ConfigurationError("CLASSIFIER_ENDPOINT_OVERRIDE is unsupported")
        try:
            definition = env.get("CLASSIFIER_DEFINITION", "classifiers/ai_capabilities.md")
            # Read exactly once. Paths outside bundled definitions are not policy inputs.
            root = Path(__file__).resolve().parent.parent / "classifiers"
            path = Path(definition)
            path = (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ConfigurationError("CLASSIFIER_DEFINITION must be a bundled definition")
            config = cls(
                aiab_mode=env.get("AIAB_MODE", "local"),
                aiab_id=env.get("AIAB_ID", "aiab-dev"),
                enforcement_mode=env.get("ENFORCEMENT_MODE", "monitor"),
                block_threshold=float(env.get("BLOCK_THRESHOLD", "0.8")),
                inference_url=env.get("INFERENCE_URL", "http://inference:8080").rstrip("/"),
                listen_port=int(
                    env.get("LISTEN_PORT", "8080" if env.get("AIAB_MODE") == "tinfoil" else "8443")
                ),
                cors_allow_origin=env.get("CORS_ALLOW_ORIGIN") or None,
                sample_rate=float(env.get("SAMPLE_RATE", "0.05")),
                report_period_seconds=int(env.get("REPORT_PERIOD_SECONDS", "600")),
                report_history=int(env.get("REPORT_HISTORY", "100")),
                tinfoil_api_key=env.get("TINFOIL_API_KEY") or None,
                classifier_model=env.get("CLASSIFIER_MODEL")
                or (
                    "gpt-oss-safeguard-120b"
                    if env.get("TINFOIL_API_KEY") or env.get("AIAB_MODE") == "tinfoil"
                    else "mock-keywords/v1"
                ),
                classifier_definition=definition,
                definition_text=path.read_text(encoding="utf-8"),
                log_level=env.get("LOG_LEVEL", "INFO").upper(),
            )
        except (ValueError, OSError, UnicodeError):
            raise ConfigurationError("Invalid AIAB startup configuration") from None
        config.validate()
        return config

    def validate(self) -> None:
        if self.aiab_mode not in {"local", "tinfoil"}:
            raise ConfigurationError("AIAB_MODE must be local or tinfoil")
        if self.aiab_mode == "tinfoil" and not self.tinfoil_api_key:
            raise ConfigurationError("Tinfoil mode requires TINFOIL_API_KEY")
        if not re_model_id(self.classifier_model):
            raise ConfigurationError("Invalid CLASSIFIER_MODEL identifier")
        if self.enforcement_mode not in {"monitor", "block"}:
            raise ConfigurationError("ENFORCEMENT_MODE must be monitor or block")
        if not self.aiab_id or len(self.aiab_id) > 128:
            raise ConfigurationError("AIAB_ID must contain 1 to 128 characters")
        if not 1 <= self.listen_port <= 65535:
            raise ConfigurationError("LISTEN_PORT is outside the valid range")
        if self.report_period_seconds < 1 or self.report_history < 1:
            raise ConfigurationError("Report period and history must be positive")
        for value in (self.sample_rate, self.block_threshold):
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ConfigurationError("Probabilities must be finite values in [0, 1]")
        origin(self.inference_url)
        parsed = urlsplit(self.inference_url)
        if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ConfigurationError("INFERENCE_URL must be a fixed origin without path or query")
        if self.cors_allow_origin and origin(self.cors_allow_origin) != self.cors_allow_origin:
            raise ConfigurationError("CORS_ALLOW_ORIGIN must be one exact origin")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("Unsupported LOG_LEVEL")


def re_model_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "_./:-")
            for character in value
        )
    )
