"""Start the proxy and report lifecycle from one frozen boot configuration."""

import uvicorn

from aiab.config import Config, ConfigurationError
from aiab.keys import ephemeral_tls_files
from aiab.logging_guard import event
from aiab.proxy import create_app


def main() -> None:
    """Start local TLS with ephemeral in-memory certificate/key material."""
    try:
        config = Config.from_env()
        app = create_app(config)
        with ephemeral_tls_files() as (certificate, private_key):
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=config.listen_port,
                ssl_certfile=certificate,
                ssl_keyfile=private_key,
                access_log=False,
                log_config=None,
                server_header=False,
            )
    except (ConfigurationError, RuntimeError) as exc:
        event("startup_failed")
        # ConfigurationError messages and this module's platform error are static text.
        message = str(exc) if isinstance(exc, ConfigurationError) else "Local TLS is unavailable"
        raise SystemExit(f"AIAB startup failed: {message}") from None


if __name__ == "__main__":
    main()
