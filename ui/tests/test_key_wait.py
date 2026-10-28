import json
import time

from server import app as ui


def test_key_marker_is_remembered_and_expires(tmp_path, monkeypatch):
    marker = tmp_path / ".key-submission.json"
    monkeypatch.setattr(ui, "_key_marker", lambda: marker)
    assert ui._key_wait() is None
    ui._remember_key("lab", "k=", {"username": "teddy"}, "pr/1")
    wait = ui._key_wait()
    assert wait["party"] == "lab" and wait["key"] == "k=" and wait["pr_url"] == "pr/1"
    stale = json.loads(marker.read_text())
    stale["at"] = time.time() - ui.KEY_WAIT_MAX - 1
    marker.write_text(json.dumps(stale))
    assert ui._key_wait() is None
    assert not marker.exists()
    ui._forget_key()  # a second time is harmless
