"""OCI inspection rejects build-host bytecode even inside compressed layer blobs."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from deploy.inspect_oci import inspect_oci


def write_oci(path: Path, member_name: str, *, directory: bool = False) -> None:
    def add(archive: tarfile.TarFile, name: str, data: bytes) -> None:
        member = tarfile.TarInfo(name)
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as layer:
        if directory:
            member = tarfile.TarInfo(member_name)
            member.type = tarfile.DIRTYPE
            layer.addfile(member)
        else:
            add(layer, member_name, b"example")
    layer_bytes = buffer.getvalue()
    layer_digest = hashlib.sha256(layer_bytes).hexdigest()
    manifest = json.dumps({"layers": [{"digest": "sha256:" + layer_digest}]}).encode()
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    index = json.dumps(
        {
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + manifest_digest,
                }
            ]
        }
    ).encode()
    with tarfile.open(path, "w") as archive:
        add(archive, "index.json", index)
        add(archive, "blobs/sha256/" + manifest_digest, manifest)
        add(archive, "blobs/sha256/" + layer_digest, layer_bytes)


@pytest.mark.parametrize(
    "name", ["app/aiab/__pycache__/main.py", "app/aiab/main.pyc", "app/aiab/main.pyo"]
)
def test_bytecode_in_oci_layers_is_rejected(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.tar"
    write_oci(archive, name)
    with pytest.raises(ValueError, match="Forbidden"):
        inspect_oci(str(archive))


def test_source_only_oci_layer_passes(tmp_path: Path) -> None:
    archive = tmp_path / "good.tar"
    write_oci(archive, "app/aiab/main.py")
    assert inspect_oci(str(archive)) == 1


def test_only_empty_base_cache_directories_are_allowed(tmp_path: Path) -> None:
    archive = tmp_path / "base.tar"
    write_oci(archive, "usr/local/lib/python3.12/__pycache__", directory=True)
    assert inspect_oci(str(archive)) == 1
    for name in ("app/aiab/__pycache__", "usr/local/lib/python3.12/__pycache__/nested"):
        write_oci(archive, name, directory=True)
        with pytest.raises(ValueError, match="Forbidden"):
            inspect_oci(str(archive))
