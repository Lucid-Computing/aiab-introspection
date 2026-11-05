import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server.workbench_admin import TRUST, main, provision
from server.workbench_api import WorkbenchAPI


def test_cli_requires_explicit_import_trust_and_never_mints_consent(tmp_path, capsys):
    key = Ed25519PrivateKey.generate()
    value = {
        "actor": "authority",
        "public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        "roles": ["roster_authority", "release_authority", "source_authority"],
    }
    with pytest.raises(ValueError, match="trust acknowledgement"):
        provision(tmp_path, "identity", value)
    value["trust"] = TRUST
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(value))
    main(["--directory", str(tmp_path), "identity", str(path)])
    assert json.loads(capsys.readouterr().out)["trust"] == TRUST
    for actor in ["a", "b"]:
        subject_key = Ed25519PrivateKey.generate()
        provision(
            tmp_path,
            "identity",
            {
                "trust": TRUST,
                "actor": actor,
                "public_key": base64.b64encode(
                    subject_key.public_key().public_bytes_raw()
                ).decode(),
            },
        )
    provision(
        tmp_path,
        "frozenroster",
        {
            "trust": TRUST,
            "authority": "authority",
            "roster_id": "roster",
            "subjects": ["a", "b"],
            "scope_id": "org",
            "source_commitment": "independent-directory-import",
        },
    )
    with pytest.raises(ValueError):
        provision(
            tmp_path,
            "frozenroster",
            {
                "trust": TRUST,
                "authority": "authority",
                "roster_id": "roster",
                "subjects": ["a"],
                "scope_id": "org",
                "source_commitment": "independent-directory-import",
            },
        )
    service = WorkbenchAPI(tmp_path, lambda _: None)
    assert service.governance.plans("authority") == []
    assert service.governance.history("authority") == []
    assert not (tmp_path / "source-grants.json").exists()
    assert not list(tmp_path.glob("*private*"))


def test_invalid_roles_and_source_authority_are_denied(tmp_path):
    key = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    with pytest.raises(ValueError, match="Unknown role"):
        provision(
            tmp_path,
            "identity",
            {"trust": TRUST, "actor": "owner", "public_key": key, "roles": ["superuser"]},
        )
    provision(tmp_path, "identity", {"trust": TRUST, "actor": "owner", "public_key": key})
    with pytest.raises(ValueError, match="source_authority"):
        provision(tmp_path, "sourcegrant", {"trust": TRUST, "authority": "owner"})
