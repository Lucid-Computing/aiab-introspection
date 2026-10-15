"""The auditor rejects forged evidence and exposes the limits of fetched history."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from aiab.classifier import MockClassifier
from aiab.config import Config
from aiab.keys import SigningKey
from aiab.reporter import Reporter, canonical_json
from auditor.checkpoint import (
    CHECKPOINT_FIELDS,
    checkpoint_warnings,
    load_checkpoint,
    save_checkpoint,
)
from auditor.cli import fetch_artifacts, format_rollup, local_origin, main
from auditor.verify import VerificationError, strict_json, verify_reports

START = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Artifacts:
    reporter: Reporter
    reports: list[dict[str, Any]]

    @property
    def identity(self) -> dict[str, str]:
        return self.reporter.public_identity

    @property
    def manifest(self) -> dict[str, Any]:
        return self.reporter.manifest

    def sign(self, reports: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
        return [
            {
                "report": canonical_json(report),
                "signature": self.reporter.signing_key.sign(canonical_json(report)),
            }
            for report in (self.reports if reports is None else reports)
        ]


def artifacts(mode: str = "monitor", count: int = 2) -> Artifacts:
    config = replace(Config.from_env({}), enforcement_mode=mode, report_period_seconds=60)
    reporter = Reporter(config, MockClassifier(), clock=lambda: 0, wall_time=START)
    reporter.clock = lambda: count * 60 + config.processing_timeout_seconds
    reports = [strict_json(envelope["report"]) for envelope in reporter.reports]
    for report in reports:
        report.update(
            requests_total=3,
            requests_forwarded=3 if mode == "monitor" else 1,
            requests_selected=3,
            requests_classified=2,
            requests_flagged=1,
            requests_blocked=0 if mode == "monitor" else 1,
            classifier_errors=1,
        )
    return Artifacts(reporter, reports)


def verified(data: Artifacts, reports: list[dict[str, Any]] | None = None) -> Any:
    return verify_reports(data.identity, data.manifest, data.sign(reports))


@pytest.fixture(autouse=True)
def clear_expected_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPECTED_AIAB_DIGEST", raising=False)
    monkeypatch.delenv("EXPECTED_POLICY_DIGEST", raising=False)


@pytest.mark.parametrize("mode", ["monitor", "block"])
def test_signature_and_rollup_count_unique_periods(mode: str) -> None:
    data = artifacts(mode)
    result = verified(data, [*data.reports, data.reports[0]])
    assert len(result.reports) == 2
    output = format_rollup(result)
    assert f"[{mode}]: 3 selected of 3 admitted at this proxy" in output
    assert "2 successfully classified, 1 classification failures, 1 flagged (50.0%" in output
    assert "Totals (2 unique periods" in output
    assert "6 selected of 6 admitted at this proxy; 4 successfully classified" in output
    assert "not token/compute share or verified research prevalence" in output
    assert "not population estimates" in output
    assert "Small samples and classification failures limit interpretation" in output


def test_rollup_zero_successes_is_na() -> None:
    data = artifacts(count=1)
    data.reports[0].update(requests_classified=0, requests_flagged=0, classifier_errors=3)
    output = format_rollup(verified(data))
    assert "0 flagged (N/A of successful classifications)" in output
    assert "3 classification failures" in output


def test_tamper_signature_and_wrong_public_key() -> None:
    data = artifacts()
    envelopes = data.sign()
    envelopes[0]["report"] = envelopes[0]["report"].replace(
        '"requests_total":3', '"requests_total":9'
    )
    with pytest.raises(VerificationError, match="signature"):
        verify_reports(data.identity, data.manifest, envelopes)
    identity = {**data.identity, "public_key": SigningKey().public_key}
    with pytest.raises(VerificationError, match="signature"):
        verify_reports(identity, data.manifest, data.sign())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "other/v1"),
        ("aiab_id", "another-box"),
        ("boot_id", "another-boot"),
        ("policy_digest", "0" * 64),
        ("classifier_id", "different-classifier"),
        ("enforcement_mode", "block"),
        ("sequence", 0),
        ("sequence", True),
        ("requests_total", True),
        ("requests_forwarded", -1),
        ("requests_classified", 3),
        ("requests_flagged", 3),
        ("requests_blocked", 1),
        ("requests_selected", 4),
        ("requests_total", 2),
        ("classifier_errors", 1.0),
        ("period_start", "2026-01-01 00:00:00+00:00"),
        ("period_start", "2026-01-01T00:00:00"),
        ("period_start", "2026-01-01T00:01:00+00:00"),
        ("period_end", "2026-01-01T00:02:00+00:00"),
        ("content", "private-body-must-not-be-released"),
    ],
)
def test_signed_invalid_report_is_rejected(field: str, value: Any) -> None:
    data = artifacts(count=1)
    data.reports[0][field] = value
    with pytest.raises(VerificationError):
        verified(data)


def test_block_must_select_all_requests() -> None:
    data = artifacts("block", count=1)
    data.reports[0].update(requests_total=4)
    with pytest.raises(VerificationError, match="counter"):
        verified(data)


def test_report_exact_schema_missing_field_noncanonical_and_duplicate_json() -> None:
    data = artifacts(count=1)
    incomplete = {key: value for key, value in data.reports[0].items() if key != "requests_total"}
    with pytest.raises(VerificationError, match="exactly"):
        verified(data, [incomplete])
    for serialized in (
        json.dumps(data.reports[0]),
        canonical_json(data.reports[0]).replace('"sequence":1', '"sequence":1,"sequence":1'),
    ):
        envelope = {"report": serialized, "signature": data.reporter.signing_key.sign(serialized)}
        with pytest.raises(VerificationError):
            verify_reports(data.identity, data.manifest, [envelope])


@pytest.mark.parametrize("value", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', "[invalid]"])
def test_strict_json_rejects_ambiguous_or_invalid_numbers(value: str) -> None:
    with pytest.raises(VerificationError):
        strict_json(value)


def test_manifest_identity_and_approved_digest_linkage() -> None:
    data = artifacts()
    result = verify_reports(
        data.identity,
        data.manifest,
        data.sign(),
        expected_policy_digest=data.identity["policy_digest"],
    )
    assert len(result.reports) == 2
    with pytest.raises(VerificationError, match="approved"):
        verify_reports(data.identity, data.manifest, data.sign(), expected_policy_digest="0" * 64)
    with pytest.raises(VerificationError, match="policy digest mismatch"):
        verify_reports(data.identity, {**data.manifest, "sample_rate": 0.99}, data.sign())
    with pytest.raises(VerificationError, match="identity schema"):
        verify_reports({**data.identity, "content": "secret"}, data.manifest, data.sign())


def test_conflicting_duplicate_out_of_order_overlap_and_missing_periods() -> None:
    data = artifacts(count=4)
    conflicting = {**data.reports[0], "requests_flagged": 0}
    with pytest.raises(VerificationError, match="Conflicting"):
        verified(data, [data.reports[0], conflicting])
    with pytest.raises(VerificationError, match="ordering"):
        verified(data, list(reversed(data.reports)))
    overlapping = {**data.reports[0], "sequence": 2}
    with pytest.raises(VerificationError, match="ordering"):
        verified(data, [data.reports[0], overlapping])
    result = verified(data, [data.reports[0], data.reports[3]])
    assert "Missing sequences 2 through 3" in result.warnings
    assert "Time gap before sequence 4" in result.warnings
    shifted = {
        **data.reports[1],
        "period_start": (START + timedelta(seconds=180)).isoformat(),
        "period_end": (START + timedelta(seconds=240)).isoformat(),
    }
    assert "Time gap before sequence 2" in verified(data, [data.reports[0], shifted]).warnings


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", "https://localhost:8443"),
        ("127.0.0.1", "https://127.0.0.1:8443"),
        ("localhost:9123", "https://localhost:9123"),
        ("https://localhost:8443/", "https://localhost:8443"),
        ("https://127.0.0.1:443", "https://127.0.0.1:443"),
    ],
)
def test_local_host_parser(host: str, expected: str) -> None:
    assert local_origin(host) == expected


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "localhost.example.com",
        "localhost@evil.example",
        "https://evil.example@localhost",
        "https://localhost@evil.example",
        "https://localhost\\@evil.example",
        "https://localhost/path",
        "localhost?next=evil.example",
        "localhost#fragment",
        "http://localhost",
        "https://localhost:0",
        "https://localhost:65536",
        "https://localhost:",
        "127.1",
        "2130706433",
        "127.0.0.2",
        "localhost.",
        "[::1]",
        "https://local%68ost",
        "https://localhost\nevil.example",
        " https://localhost",
        "",
    ],
)
def test_insecure_host_guard_rejects_ambiguous_nonlocal_and_plaintext_hosts(host: str) -> None:
    with pytest.raises(VerificationError):
        local_origin(host)


def install_transport(
    monkeypatch: pytest.MonkeyPatch, data: Artifacts, envelopes: Any = None
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    original = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "localhost"
        payloads = {
            "/aiab/pubkey": data.identity,
            "/aiab/manifest": data.manifest,
            "/aiab/reports": data.sign() if envelopes is None else envelopes,
        }
        return httpx.Response(200, json=payloads[request.url.path])

    def client(**kwargs: Any) -> httpx.Client:
        assert kwargs["verify"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("auditor.cli.httpx.Client", client)
    return requests


@pytest.mark.parametrize("command", ["verify", "report", "rollup"])
def test_cli_commands_validate_and_warn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    data = artifacts()
    requests = install_transport(monkeypatch, data)
    main([command, "--host", "localhost", "--insecure-local"])
    output = capsys.readouterr()
    assert "INSECURE LOCAL MODE" in output.err
    assert "Policy digest: " + data.identity["policy_digest"] in output.out
    assert "Image digest: UNVERIFIED" in output.out
    assert requests[0].url.path == requests[-1].url.path == "/aiab/pubkey"
    if command == "report":
        reports = [strict_json(line) for line in output.out.splitlines() if line.startswith("{")]
        assert len(reports) == 2
    if command == "rollup":
        assert "Totals (2 unique periods" in output.out


def test_attested_and_invalid_local_cli_never_connect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never_connect(**_: Any) -> None:
        pytest.fail("Invalid or unimplemented verification mode attempted a connection")

    monkeypatch.setattr("auditor.cli.httpx.Client", never_connect)
    with pytest.raises(SystemExit) as error:
        main(["verify", "--host", "public.example"])
    assert error.value.code == 1
    assert "independently approved release and policy digests" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["rollup", "--host", "public.example", "--insecure-local"])


def test_reboot_during_fetch_requires_retry() -> None:
    data, restarted = artifacts(), artifacts()
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if request.url.path == "/aiab/pubkey":
            return httpx.Response(200, json=data.identity if count == 1 else restarted.identity)
        if request.url.path == "/aiab/manifest":
            return httpx.Response(200, json=data.manifest)
        return httpx.Response(200, json=data.sign())

    with httpx.Client(
        base_url="https://localhost", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(VerificationError, match="while fetching"):
            fetch_artifacts(client, reports=True)


def test_redirect_never_leaves_local_host(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = httpx.Client
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://remote.example/aiab/pubkey"})

    monkeypatch.setattr(
        "auditor.cli.httpx.Client",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(SystemExit):
        main(["verify", "--host", "localhost", "--insecure-local"])
    assert len(requests) == 1
    assert requests[0].url.host == "localhost"
    assert "unsuccessful status" in capsys.readouterr().err


def test_checkpoint_first_use_saved_metadata_only_after_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    data = artifacts()
    install_transport(monkeypatch, data)
    state = tmp_path / "checkpoint.json"
    main(["rollup", "--host", "localhost", "--insecure-local", "--state-file", str(state)])
    output = capsys.readouterr()
    assert "First checkpoint use: earlier continuity is unknown" in output.err
    assert "incomplete evidence, not proof of malicious behavior" in output.err
    value = load_checkpoint(state)
    assert value is not None and value.keys() == CHECKPOINT_FIELDS
    assert value["sequence"] == 2
    assert "requests_total" not in state.read_text()
    assert state.stat().st_mode & 0o777 == 0o600


def test_invalid_report_does_not_save_or_overwrite_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = artifacts()
    state = tmp_path / "checkpoint.json"
    save_checkpoint(state, verified(data, [data.reports[0]]), None)
    original = state.read_bytes()
    data.reports[1]["content"] = "private-unique-body-sentinel"
    install_transport(monkeypatch, data)
    with pytest.raises(SystemExit):
        main(["rollup", "--host", "localhost", "--insecure-local", "--state-file", str(state)])
    output = capsys.readouterr()
    assert "private-unique-body-sentinel" not in output.out + output.err
    assert state.read_bytes() == original
    assert list(tmp_path.iterdir()) == [state]


def test_checkpoint_restart_changes_missing_reports_and_time_gaps(tmp_path: Path) -> None:
    data = artifacts(count=5)
    state = tmp_path / "checkpoint.json"
    save_checkpoint(state, verified(data, [data.reports[0]]), None)
    previous = load_checkpoint(state)
    result = verified(data, [data.reports[3], data.reports[4]])
    warnings = checkpoint_warnings(result, previous, now=START + timedelta(seconds=330))
    assert "Missing sequences 2 through 3 after checkpoint" in warnings
    assert "Time gap between checkpoint and new reports" in warnings
    restarted = artifacts("block", count=1)
    result = verified(restarted)
    warnings = checkpoint_warnings(result, previous, now=START + timedelta(seconds=100))
    assert any("Boot changed" in warning for warning in warnings)
    assert any("Signing key changed" in warning for warning in warnings)
    assert any("Policy changed" in warning for warning in warnings)
    save_checkpoint(state, result, previous)
    assert load_checkpoint(state)["boot_id"] == restarted.identity["boot_id"]


def test_checkpoint_same_boot_cannot_recede_or_be_cleared(tmp_path: Path) -> None:
    data = artifacts(count=4)
    state = tmp_path / "checkpoint.json"
    save_checkpoint(state, verified(data), None)
    original = state.read_bytes()
    previous = load_checkpoint(state)
    for reports in ([data.reports[0]], []):
        result = verified(data, reports)
        warnings = checkpoint_warnings(result, previous, now=START + timedelta(seconds=360))
        assert any("receded" in warning for warning in warnings)
        save_checkpoint(state, result, previous)
        assert state.read_bytes() == original


def test_checkpoint_up_to_date_overdue_and_conflicting_times(tmp_path: Path) -> None:
    data = artifacts()
    state = tmp_path / "checkpoint.json"
    save_checkpoint(state, verified(data, [data.reports[0]]), None)
    previous = load_checkpoint(state)
    result = verified(data)
    assert checkpoint_warnings(result, previous, now=START + timedelta(seconds=150)) == []
    assert any(
        "overdue" in warning
        for warning in checkpoint_warnings(result, previous, now=START + timedelta(seconds=211))
    )
    previous["period_end"] = (START + timedelta(seconds=61)).isoformat()
    with pytest.raises(VerificationError, match="conflicting period end"):
        checkpoint_warnings(result, previous, now=START + timedelta(seconds=150))


def test_checkpoint_invalid_json_never_deserialized_as_code(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"schema":"__import__(os)","extra":"not accepted"}')
    with pytest.raises(VerificationError, match="schema"):
        load_checkpoint(path)
    link = tmp_path / "symlink.json"
    link.symlink_to(path)
    with pytest.raises(VerificationError, match="symbolic link"):
        load_checkpoint(link)


def test_no_reports_rollup_explains_empty_evidence() -> None:
    data = artifacts(count=0)
    result = verified(data)
    assert "Totals (0 unique periods" in format_rollup(result)
    assert "N/A" in format_rollup(result)
    assert any(
        "No finalized reports" in warning
        for warning in checkpoint_warnings(result, None, now=START)
    )
