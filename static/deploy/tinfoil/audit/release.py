"""Coordinate an audit release; CI builds/measures, never runs in the enclave.

Default is read-only planning. --execute builds the image, publishes and measures the
config, tells Tinfoil to run the new tag, then checks that the live box has the expected
identity and answers health. Tinfoil running the new tag is the release; nothing before
the deploy compares the box or the config repository with the repository's record, so a
record that fell behind never blocks the next release. Credentials stay in the
operator's existing gh/Tinfoil stores. Each attempt has a new private output directory
and a durable stage journal. Failures never get a success record.
"""

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from aiab.audit.plan import Trust
from aiab.audit.protocol import canonical, digest
from aiab.config import read_private_key
from auditor.audit import AuditClient


def command(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Command failed: " + args[0])
    return result.stdout.strip()


def workflow(repo, name, ref, output, *inputs):
    result = command("gh", "workflow", "run", name, "--repo", repo, "--ref", ref, *inputs)
    match = re.search(r"/actions/runs/(\d+)", result)
    if not match:
        raise RuntimeError(
            "Workflow dispatch did not return a run ID; inspect GitHub before retrying"
        )
    run_id = match[1]
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        state = json.loads(
            command(
                "gh", "run", "view", run_id, "--repo", repo, "--json", "status,conclusion,headSha"
            )
        )
        if state["status"] == "completed":
            if state["conclusion"] != "success":
                raise RuntimeError("Workflow failed: " + run_id)
            (output / (name + ".json")).write_bytes(canonical({"run_id": run_id, **state}) + b"\n")
            return run_id, state["headSha"]
        time.sleep(10)
    raise RuntimeError("Workflow timeout; inspect run " + run_id)


def render(config, trust, build, release_tag):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", release_tag):
        raise ValueError("Invalid release tag")
    if (
        build.get("schema") != "aiab-build/v1"
        or build.get("digest") != build.get("reproduced_digest")
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", build.get("digest", ""))
        or not re.fullmatch(r"ghcr.io/[a-z0-9-]+/[a-z0-9._-]+", build.get("image", ""))
    ):
        raise ValueError("Invalid reproducible build evidence")
    config, trust = json.loads(json.dumps(config)), json.loads(json.dumps(trust))
    trust["runtime_digest"] = build["digest"]
    containers = [c for c in config["containers"] if c["name"] == "aiab"]
    if len(containers) != 1:
        raise ValueError("Expected one audit container")
    container = containers[0]
    container["image"] = build["image"] + "@" + build["digest"]
    # Distinct tags need distinct measurements: the SDK binds Sigstore evidence
    # to the selected tag, while evidence lookup is by measurement digest.
    container["env"] = [e for e in container["env"] if "AIAB_RELEASE_TAG" not in e]
    container["env"].append({"AIAB_RELEASE_TAG": release_tag})
    entries = [e for e in container["env"] if "AIAB_AUDIT_TRUST_JSON" in e]
    if len(entries) != 1:
        raise ValueError("Expected one measured trust source")
    entries[0]["AIAB_AUDIT_TRUST_JSON"] = canonical(trust).decode()
    if "plan_digest" in build:
        from aiab.baked.bundle import load_bundle
        from aiab.baked.runtime import Runtime

        bundle = load_bundle(Path(__file__).resolve().parents[4] / "plan", trust)
        if bundle.digest != build["plan_digest"]:
            raise ValueError("Built plan differs from signed source bundle")
        container["command"] = ["python", "-m", "aiab.baked.main"]
        # Keep the route to return a clear authenticated 405 rather than shim HTML.
        manifest = Runtime(bundle, trust, None).manifest
    else:
        manifest = Trust(**trust).manifest()
    return config, trust, manifest


class ControlPlane:
    def __init__(self, path):
        settings = json.loads(path.read_text())
        if settings["controlplane_url"].rstrip("/") != "https://api.tinfoil.sh":
            raise ValueError("Unexpected control plane origin")
        self.client = httpx.Client(
            base_url="https://api.tinfoil.sh",
            timeout=60,
            headers={"Authorization": "Bearer " + settings["api_key"], "User-Agent": "tinfoil-cli"},
            follow_redirects=False,
        )

    def request(self, method, path, value=None):
        response = self.client.request(method, path, json=value)
        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                "Control plane rejected operation: HTTP " + str(response.status_code)
            )
        return response.json()


def create_box(cp, template_id, name, tag, repo):
    """Create a separate confidential container; never replace/promote another box.

    Use the existing container only for placement and org secret references. Names,
    domains, SSH/debug settings and instance IDs are not copied.
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name) or name == "default":
        raise ValueError("Box name must be 1–40 lowercase letters, digits or hyphens (not default)")
    template = cp.request("GET", "/api/containers/" + template_id)
    if template["repo"].lower() != repo.lower():
        raise ValueError("Template container uses a different config repository")
    variables = template.get("variables") or {}
    # The control plane returns this JSON object as base64-encoded bytes.
    if isinstance(variables, str):
        variables = json.loads(base64.b64decode(variables, validate=True))
    if not isinstance(variables, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in variables.items()
    ):
        raise ValueError("Invalid container variables")
    created = cp.request(
        "POST",
        "/api/containers",
        {
            "name": name,
            "repo": repo,
            "tag": tag,
            "host_name": template["host_name"],
            "secrets": template.get("secrets") or [],
            "variables": variables,
            "debug": False,
            "disable_cc_mode": False,
            "mark_latest_release": False,
        },
    )
    return created["id"], created["domain"]


async def verify(release, token_file):
    client = AuditClient(
        release["host"],
        release["repo"],
        release["release_digest"],
        release["policy_digest"],
        read_private_key(str(token_file), "AUDIT_TOKEN"),
    )
    try:
        await client.start()
        status, health = await client.request("GET", "/aiab/health")
        if status != 200 or health.get("status") != "ready":
            raise RuntimeError("Deployment not ready")
        return {"identity": client.identity, "health": health}
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", default="feat/signed-plan-audit-execution")
    parser.add_argument(
        "--source-commit", help="Exact approved source; build workflow stays on --source-ref"
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--key-directory",
        type=Path,
        required=True,
        help="Local synthetic-demo role keys/tokens; never uploaded to CI",
    )
    parser.add_argument("--instance", default="28c10708-7f8b-4981-8280-dca928a2654b")
    parser.add_argument("--new-box", help="Create a named box instead of updating --instance")
    args = parser.parse_args()
    if args.source_commit and not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("Invalid source commit")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
        parser.error("Tag must be vMAJOR.MINOR.PATCH")
    if not re.fullmatch(r"[0-9a-f-]{36}", args.instance):
        parser.error("Invalid instance ID")
    if args.new_box and (
        not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.new_box) or args.new_box == "default"
    ):
        parser.error("Invalid box name")
    root = Path(__file__).resolve().parents[4]
    baseline = Path(__file__).parent
    if args.new_box and (baseline / "boxes" / (args.new_box + ".json")).exists():
        raise ValueError("Box name already registered; use a new name")
    source_repo = "Lucid-Computing/aiab-introspection"
    prior = json.loads((baseline / "release.json").read_text())
    repo = prior["repo"]
    sha = command("git", "rev-parse", "HEAD", cwd=root)
    if command("git", "status", "--porcelain", cwd=root):
        raise RuntimeError("Commit local changes before planning a release")
    remote_sha = command(
        "gh",
        "api",
        "repos/" + source_repo + "/commits/" + (args.source_commit or args.source_ref),
        "--jq",
        ".sha",
    )
    if remote_sha != sha:
        raise RuntimeError("Local source differs from selected remote ref")
    args.output = args.output.resolve()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    journal = {
        "schema": "aiab-release-attempt/v1",
        "source_commit": sha,
        "tag": args.tag,
        "instance": args.instance,
        "new_box": args.new_box,
        "previous_release": prior,
        "status": "planned",
    }

    def record(stage, **values):
        journal.update(stage=stage, **values)
        tmp = args.output / "attempt.tmp"
        tmp.write_bytes(canonical(journal) + b"\n")
        tmp.replace(args.output / "attempt.json")
        print(stage, flush=True)

    record("planned")
    if not args.execute:
        print("No changes made. Repeat with --execute and a new output directory.")
        return
    cp = ControlPlane(Path.home() / ".tinfoil/config.json")
    try:
        record("building", status="in_progress")
        image = "ghcr.io/lucid-computing/aiab-introspection-enclave"
        run_id, _ = workflow(
            source_repo,
            "build-image.yml",
            args.source_ref,
            args.output,
            "-f",
            "image=" + image,
            "-f",
            "source_commit=" + sha,
        )
        build_dir = args.output / "build"
        command(
            "gh",
            "run",
            "download",
            run_id,
            "--repo",
            source_repo,
            "--name",
            "audit-build",
            "--dir",
            str(build_dir),
        )
        build = json.loads((build_dir / "audit-build.json").read_text())
        if build["source_commit"] != sha or build["image"] != image:
            raise RuntimeError("Build identity mismatch")
        checkout = args.output / "config"
        command("git", "clone", "https://github.com/" + repo + ".git", str(checkout))
        # The trust the box runs with: the last recorded trust (runtime digest, model) with
        # the keys and model from static/aiab/baked/trust.json on the source ref, the file
        # the key pull requests change and the image bakes. The box compares the two at
        # start-up (static_trust_mismatch), so they must agree; the plan's signatures are
        # verified against these keys below.
        trust_in = json.loads((baseline / "trust.json").read_text())
        trust_in.update(json.loads((root / "static/aiab/baked/trust.json").read_text()))
        config, trust, manifest = render(
            json.loads((checkout / "tinfoil-config.yml").read_text()), trust_in, build, args.tag
        )
        for name, value in (
            ("tinfoil-config.yml", config),
            ("trust.json", trust),
            ("manifest.json", manifest),
        ):
            (checkout / name).write_text(json.dumps(value, indent=2) + "\n")
        (checkout / "policy-digest.txt").write_text(digest(manifest) + "\n")
        command(
            "git",
            "add",
            "tinfoil-config.yml",
            "trust.json",
            "manifest.json",
            "policy-digest.txt",
            cwd=checkout,
        )
        command(
            "git", "commit", "--allow-empty", "-m", "Release signed audit " + args.tag, cwd=checkout
        )
        config_sha = command("git", "rev-parse", "HEAD", cwd=checkout)
        command("git", "tag", args.tag, cwd=checkout)
        command("git", "push", "--atomic", "origin", "HEAD:main", args.tag, cwd=checkout)
        record("measuring", config_commit=config_sha, build=build)
        _, measured_sha = workflow(repo, "measure-release.yml", args.tag, args.output)
        if measured_sha != config_sha:
            raise RuntimeError("Measured commit mismatch")
        command(
            "gh",
            "release",
            "download",
            args.tag,
            "--repo",
            repo,
            "--pattern",
            "tinfoil.hash",
            "--dir",
            str(args.output),
        )
        release_hash = (args.output / "tinfoil.hash").read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", release_hash):
            raise RuntimeError("Invalid release measurement")
        release = {
            **prior,
            "tag": args.tag,
            "reviewed_config_commit": config_sha,
            "release_digest": "sha256:" + release_hash,
            "policy_digest": digest(manifest),
            "application_source_commit": sha,
            **(
                {"image_digest": build["digest"], "plan_digest": build["plan_digest"]}
                if "plan_digest" in build
                else {}
            ),
        }
        record("deploying", target_release=release)
        if args.new_box:
            instance, host = create_box(
                cp, args.instance, args.new_box, args.tag, prior.get("control_plane_repo", repo)
            )
            release.update(host=host, instance_id=instance, box_name=args.new_box)
        else:
            instance = args.instance
            cp.request(
                "POST",
                "/api/containers/" + instance + "/update",
                {"tag": args.tag, "hold": False},
            )
        # Record only after the provider accepted a create/update. Keep failed deployments
        # discoverable without ever claiming their health/attestation check succeeded.
        (args.output / "release.json").write_bytes(canonical(release) + b"\n")
        record("deploying", instance=instance, target_release=release)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            live = cp.request("GET", "/api/containers/" + instance)
            if live.get("current_tag") == args.tag and live.get("status") == "running":
                break
            if live.get("update_status") == "failed" or live.get("status") == "failed":
                raise RuntimeError("Deployment failed")
            time.sleep(10)
        else:
            raise RuntimeError("Deployment timeout")
        record("verifying")
        deadline = time.monotonic() + 180
        while True:
            try:
                verification = asyncio.run(verify(release, args.key_directory / "lab.token"))
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Live identity/readiness verification failed") from None
                time.sleep(10)
        # No synthetic run here: what a plan decides is the parties' business, and the box's
        # identity, trust and readiness are verified above. Runs happen from the UI.
        record("verified", status="succeeded", verification=verification)
    except Exception as exc:
        record(
            "failed",
            failed_stage=journal.get("stage"),
            status="failed",
            error=type(exc).__name__,
            reason=str(exc),
            recovery="Inspect stage/provider state; no automatic rollback or resubmission",
        )
        raise SystemExit(
            f"Release failed at stage {journal.get('failed_stage')}: "
            f"{type(exc).__name__}: {exc}. The attempt journal has the details."
        ) from None
    finally:
        cp.client.close()


if __name__ == "__main__":
    os.umask(0o077)
    main()
