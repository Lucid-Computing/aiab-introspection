"""Allowlist operational log fields; never log bodies or raw provider exceptions."""

import logging

_EVENTS = frozenset(
    {
        "startup_ready",
        "classifier_verified",
        "startup_failed",
        "stream_rejected",
        "classifier_error",
        "upstream_error",
        "attestation_failed",
        "internal_error",
        "queue_exhausted",
    }
)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level))
    logging.getLogger("aiab").setLevel(getattr(logging, level))
    # URLs, exception strings and wire traces can contain private conversation data.
    for name in (
        "httpx",
        "httpcore",
        "httpx2",
        "httpcore2",
        "openai",
        "tinfoil",
        "ehbp",
        "sigstore",
        "urllib3",
        "uvicorn.access",
        "uvicorn.error",
    ):
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.propagate = False
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.CRITICAL + 1)


def event(category: str, *, count: int = 1) -> None:
    if category not in _EVENTS or type(count) is not int or count < 0:
        raise ValueError("Operational log fields must be allowlisted")
    logging.getLogger("aiab").info("event=%s count=%d", category, count)
