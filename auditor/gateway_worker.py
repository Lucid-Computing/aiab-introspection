"""Private gateway subprocess; input and output travel only over anonymous pipes."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys


def main() -> None:
    logging.disable(logging.CRITICAL)
    # Suppress even libraries that write directly to stdout/stderr. Only our
    # final bounded response uses the saved private output pipe descriptor.
    output_fd = os.dup(1)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    try:
        from auditor.gateway import MAX_BODY, GatewayConfig, forward_verified, unavailable
        from auditor.verify import strict_json

        value = strict_json(sys.stdin.buffer.read(2_000_001))
        body = base64.b64decode(value["body"], validate=True)
        if len(body) > MAX_BODY:
            raise ValueError
        config = GatewayConfig(**value["config"])
        response = forward_verified(config, value["method"], value["path"], body)
        if len(response.body) > 2_097_152 or config.token.encode() in response.body:
            response = unavailable()
        output = json.dumps(
            {
                "status": response.status_code,
                "body": base64.b64encode(response.body).decode("ascii"),
            }
        ).encode()
        while output:
            output = output[os.write(output_fd, output) :]
    except Exception:
        # Parent returns a fixed 503 for malformed/empty output.
        pass
    finally:
        os.close(output_fd)


if __name__ == "__main__":
    main()
