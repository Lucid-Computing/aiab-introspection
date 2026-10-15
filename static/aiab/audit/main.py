"""Start the production audit service; no mock or unverified transport fallback."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from aiab.audit.api import create_app
from aiab.audit.logger import SignedTextVerifier
from aiab.audit.model import PinnedModel
from aiab.audit.plan import Trust
from aiab.audit.protocol import DIGEST, KEY, MODEL, AuditError, closed, decode, validate
from aiab.audit.runtime import AuditRuntime
from aiab.config import read_private_key
from aiab.logging_guard import configure_logging

TRUST_SCHEMA = closed(
    {
        "runtime_digest": DIGEST,
        "lab_key": KEY,
        "auditor_key": KEY,
        "logger_key": KEY,
        "model": MODEL,
    }
)


def secret(name: str) -> str:
    if name in os.environ and name + "_FILE" in os.environ:
        raise AuditError("ambiguous_credential")
    if name + "_FILE" in os.environ:
        return read_private_key(os.environ[name + "_FILE"], name)
    value = os.environ.get(name, "")
    if not 32 <= len(value) <= 4096 or any(not 33 <= ord(c) <= 126 for c in value):
        raise AuditError("missing_credential")
    return value


def application():
    configure_logging("INFO")
    if ("AIAB_AUDIT_TRUST_FILE" in os.environ) == ("AIAB_AUDIT_TRUST_JSON" in os.environ):
        raise AuditError("configure_one_trust_source")
    settings = decode(
        Path(os.environ["AIAB_AUDIT_TRUST_FILE"]).read_bytes()
        if "AIAB_AUDIT_TRUST_FILE" in os.environ
        else os.environ["AIAB_AUDIT_TRUST_JSON"]
    )
    validate(settings, TRUST_SCHEMA)
    trust = Trust(**settings)
    if len({trust.lab_key, trust.auditor_key, trust.logger_key}) != 3:
        raise AuditError("invalid_trust_roots")
    model = PinnedModel(trust.model, secret("TINFOIL_API_KEY"))
    runtime = AuditRuntime(trust, SignedTextVerifier(), model)
    runtime.healthy = False
    app = create_app(
        runtime,
        {role: secret("AIAB_" + role.upper() + "_TOKEN") for role in ("lab", "auditor", "logger")},
    )
    old_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        try:
            await model.start()
            runtime.healthy = True
            if "AIAB_INITIAL_PLAN_FILE" in os.environ:
                runtime.registry.register(
                    decode(Path(os.environ["AIAB_INITIAL_PLAN_FILE"]).read_bytes())
                )
            async with old_lifespan(app):
                yield
        finally:
            await model.close()

    app.router.lifespan_context = lifespan
    return app


def main():
    try:
        uvicorn.run(
            application(),
            host="0.0.0.0",
            port=int(os.environ.get("LISTEN_PORT", "8080")),
            access_log=False,
            log_config=None,
            server_header=False,
            proxy_headers=False,
        )
    except Exception:
        raise SystemExit(
            "Audit service startup failed; verify public settings and credentials"
        ) from None


if __name__ == "__main__":
    main()
