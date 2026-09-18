"""Parse and validate environment settings exactly once, then freeze them for the boot."""

from __future__ import annotations

import math
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Invalid startup configuration (messages must never include secret values)."""


ANTHROPIC_ORIGIN = "https://api.anthropic.com"
ANTHROPIC_MODEL = "claude-sonnet-4-6"
TINFOIL_MODEL = "gpt-oss-safeguard-120b"
MOCK_CLASSIFIER_MODEL = "mock-keywords/v1"


def read_private_key(filename: str, setting: str) -> str:
    """Read once through directory descriptors, refusing symlinks in every component.

    Only root or the process UID may own the 0400/0600 regular file. Return only
    the key, never retain the path, and reflect neither value in errors.
    ``setting`` is a caller-supplied constant, never an environment value.
    """
    directory_fd = None
    try:
        path = Path(filename)
        if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
            raise ValueError
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open(path.anchor, directory_flags)
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "rb") as secret:
            metadata = os.fstat(secret.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
                or metadata.st_uid not in {0, os.geteuid()}
                or not 1 <= metadata.st_size <= 4097
            ):
                raise ValueError
            value = secret.read(4098).removesuffix(b"\n")
        if not 1 <= len(value) <= 4096 or any(byte < 33 or byte > 126 for byte in value):
            raise ValueError
        return value.decode("ascii")
    except (OSError, ValueError, UnicodeError):
        raise ConfigurationError(f"{setting} must be a private regular key file") from None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def read_anthropic_key(filename: str) -> str:
    return read_private_key(filename, "ANTHROPIC_API_KEY_FILE")


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
    inference_provider: str = "openai"
    anthropic_model: str = ANTHROPIC_MODEL
    anthropic_api_key: str | None = field(default=None, repr=False)
    listen_port: int = 8443
    cors_allow_origin: str | None = None
    sample_rate: float = 0.05
    report_period_seconds: int = 600
    report_history: int = 100
    tinfoil_api_key: str | None = field(default=None, repr=False)
    classifier_model: str = MOCK_CLASSIFIER_MODEL
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
        if "TINFOIL_API_KEY_FILE" in env:
            # Presence is ambiguous even if the legacy variable is empty. Hosted
            # Compose omits it when selecting the file-only overlay.
            if "TINFOIL_API_KEY" in env:
                raise ConfigurationError("Configure only one Tinfoil credential source")
            tinfoil_key = read_private_key(env["TINFOIL_API_KEY_FILE"], "TINFOIL_API_KEY_FILE")
        else:
            # Retained for the attested deployment's repository secret and local tools.
            tinfoil_key = env.get("TINFOIL_API_KEY") or None
        provider = env.get("INFERENCE_PROVIDER", "openai")
        if provider not in {"openai", "anthropic"}:
            raise ConfigurationError("INFERENCE_PROVIDER must be openai or anthropic")
        key = None
        if provider == "anthropic":
            if "ANTHROPIC_API_KEY" in env:
                raise ConfigurationError("Use ANTHROPIC_API_KEY_FILE, never ANTHROPIC_API_KEY")
            key = read_anthropic_key(env.get("ANTHROPIC_API_KEY_FILE", ""))
        elif "ANTHROPIC_API_KEY_FILE" in env or "ANTHROPIC_MODEL" in env:
            raise ConfigurationError(
                "Anthropic configuration requires INFERENCE_PROVIDER=anthropic"
            )
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
                inference_url=env.get(
                    "INFERENCE_URL",
                    ANTHROPIC_ORIGIN if provider == "anthropic" else "http://inference:8080",
                ).rstrip("/"),
                inference_provider=provider,
                anthropic_model=env.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
                anthropic_api_key=key,
                listen_port=int(
                    env.get("LISTEN_PORT", "8080" if env.get("AIAB_MODE") == "tinfoil" else "8443")
                ),
                cors_allow_origin=env.get("CORS_ALLOW_ORIGIN") or None,
                sample_rate=float(env.get("SAMPLE_RATE", "0.05")),
                report_period_seconds=int(env.get("REPORT_PERIOD_SECONDS", "600")),
                report_history=int(env.get("REPORT_HISTORY", "100")),
                tinfoil_api_key=tinfoil_key,
                classifier_model=env.get("CLASSIFIER_MODEL")
                or (
                    TINFOIL_MODEL
                    if tinfoil_key or env.get("AIAB_MODE") == "tinfoil"
                    else MOCK_CLASSIFIER_MODEL
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
        if self.inference_provider not in {"openai", "anthropic"}:
            raise ConfigurationError("INFERENCE_PROVIDER must be openai or anthropic")
        if self.inference_provider == "anthropic":
            if self.inference_url != ANTHROPIC_ORIGIN:
                raise ConfigurationError("Anthropic inference requires its fixed HTTPS origin")
            if not self.anthropic_api_key or not re_model_id(self.anthropic_model):
                raise ConfigurationError("Anthropic inference requires a key file and valid model")
        elif self.anthropic_api_key:
            raise ConfigurationError("Anthropic key requires the Anthropic provider")
        if self.aiab_mode not in {"local", "tinfoil"}:
            raise ConfigurationError("AIAB_MODE must be local or tinfoil")
        if self.aiab_mode == "tinfoil" and not self.tinfoil_api_key:
            raise ConfigurationError("Tinfoil mode requires a classifier credential")
        if not re_model_id(self.classifier_model):
            raise ConfigurationError("Invalid CLASSIFIER_MODEL identifier")
        if self.classifier_model != MOCK_CLASSIFIER_MODEL and not self.tinfoil_api_key:
            raise ConfigurationError("Non-mock classifier requires a Tinfoil credential")
        if self.classifier_model == MOCK_CLASSIFIER_MODEL and self.tinfoil_api_key:
            raise ConfigurationError("Tinfoil credential requires a non-mock classifier model")
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
