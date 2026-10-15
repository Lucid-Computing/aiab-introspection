"""Production baked-plan service: verify bundle before starting inference or HTTP."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from aiab.audit.api import create_app
from aiab.audit.main import TRUST_SCHEMA, secret
from aiab.audit.model import PinnedModel
from aiab.audit.protocol import AuditError, decode, validate
from aiab.baked.bundle import load_bundle
from aiab.baked.runtime import Runtime
from aiab.logging_guard import configure_logging


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
    # Keys/model are static-reviewed build inputs, not supplied by the plan or client.
    fixed = decode(Path(__file__).with_name("trust.json").read_bytes())
    if {k: settings[k] for k in fixed} != fixed:
        raise AuditError("static_trust_mismatch")
    bundle = load_bundle(Path("/app/plan"), settings)
    model = PinnedModel(settings["model"], secret("TINFOIL_API_KEY"))
    runtime = Runtime(bundle, settings, model)
    runtime.healthy = False
    app = create_app(
        runtime,
        {role: secret("AIAB_" + role.upper() + "_TOKEN") for role in ("lab", "auditor", "logger")},
    )
    old = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        try:
            await model.start()
            runtime.healthy = True
            async with old(app):
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
            "Baked audit startup failed; check signed bundle, static trust and credentials"
        ) from None


if __name__ == "__main__":
    main()
