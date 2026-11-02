"""Security primitives retained when removing the legacy proxy implementation."""

import pytest

from aiab.audit.protocol import AuditError, decode
from aiab.config import ConfigurationError, read_private_key


@pytest.mark.parametrize(
    "raw", ['{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}', "NaN", "Infinity", "-Infinity"]
)
def test_strict_json_rejects_ambiguous_or_nonfinite_input(raw):
    with pytest.raises(AuditError, match="invalid_json"):
        decode(raw)


def test_private_key_permissions_and_symlinks(tmp_path):
    key = tmp_path / "role.key"
    key.write_text("synthetic-private-key\n")
    key.chmod(0o600)
    assert read_private_key(str(key), "TEST_KEY") == "synthetic-private-key"
    key.chmod(0o644)
    with pytest.raises(ConfigurationError):
        read_private_key(str(key), "TEST_KEY")
    key.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(key)
    with pytest.raises(ConfigurationError):
        read_private_key(str(alias), "TEST_KEY")
    parent_alias = tmp_path / "directory-alias"
    parent_alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ConfigurationError):
        read_private_key(str(parent_alias / "role.key"), "TEST_KEY")
