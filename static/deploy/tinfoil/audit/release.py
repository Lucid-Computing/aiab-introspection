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


PRODUCTION_REPO = "Lucid-Computing/aiab-signed-audit-enclave"
PRODUCTION_INSTANCE = "28c10708-7f8b-4981-8280-dca928a2654b"
PRODUCTION_IMAGE = "ghcr.io/lucid-computing/aiab-introspection-enclave"


def environment(prior, instance=None):
    """What differs between environments (production, staging), read from the branch's
    release.json: the Tinfoil instance to update and the image name. ``instance`` is None for
    an environment whose first measured release has not yet been used to create one; the
    release then stops after measuring. Production's release.json predates both fields, so
    its values are the literals."""
    legacy = prior.get("repo") == PRODUCTION_REPO
    instance = instance or prior.get("instance") or (PRODUCTION_INSTANCE if legacy else None)
    if instance is not None and not re.fullmatch(r"[0-9a-f-]{36}", instance):
        raise ValueError("Invalid instance ID")
    image = prior.get("image") or (PRODUCTION_IMAGE if legacy else None)
    if not image or not re.fullmatch(r"ghcr.io/[a-z0-9-]+/[a-z0-9._-]+", image):
        raise ValueError("release.json needs the environment's image name")
    return instance, image


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
                "Control plane rejected operation: HTTP "
                + str(response.status_code)
                + " on "
                + method
                + " "
                + path
                + ": "
                + response.text[:300]
            )
        return response.json()


def secret_names(config):
    """The secrets the config's audit container declares; a new instance must be granted
    them explicitly, like the CLI's --secret flags do."""
    containers = [c for c in config["containers"] if c["name"] == "aiab"]
    return list(containers[0].get("secrets", [])) if containers else []


def instance_host(view):
    """The public hostname of a container as the control plane describes it."""
    host = view.get("domain")
    if not host:
        raise RuntimeError(
            "The control plane gave no public domain for the instance; fields: "
            + ", ".join(sorted(view))
        )
    return host


def create_instance(cp, name, repo, tag, secrets):
    """Create a Tinfoil instance from a measured release and start it: what the CLI's
    ``container create`` does (POST /api/containers, then POST .../deploy). Returns the
    instance id and its public hostname, for release.json."""
    body = {"name": name, "repo": repo, "tag": tag, "mark_latest_release": True}
    if secrets:
        body["secrets"] = secrets
    created = cp.request("POST", "/api/containers", body)
    instance = created["id"]
    if not re.fullmatch(r"[0-9a-f-]{36}", instance):
        raise RuntimeError("Unexpected instance id from the control plane: " + instance)
    view = cp.request("GET", "/api/containers/" + instance)
    # Creation can already start the deployment; starting it twice returns HTTP 409.
    if view.get("status") not in {"deploying", "started", "running"}:
        cp.request("POST", "/api/containers/" + instance + "/deploy", {})
        view = cp.request("GET", "/api/containers/" + instance)
    return instance, instance_host(view)


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
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--key-directory",
        type=Path,
        required=True,
        help="Local synthetic-demo role keys/tokens; never uploaded to CI",
    )
    parser.add_argument(
        "--instance",
        default=None,
        help="Tinfoil instance to update; default: the 'instance' field of release.json",
    )
    parser.add_argument(
        "--create",
        default=None,
        metavar="NAME",
        help="For an environment without an instance: create one with this name from the "
        "measured release, start it and record its id and hostname in release.json",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
        parser.error("Tag must be vMAJOR.MINOR.PATCH")
    root = Path(__file__).resolve().parents[4]
    baseline = Path(__file__).parent
    source_repo = "Lucid-Computing/aiab-introspection"
    # Everything that differs between environments (production, staging) is in release.json
    # on the environment's branch: the public config repo, the Tinfoil instance and the image.
    prior = json.loads((baseline / "release.json").read_text())
    repo = prior["repo"]
    try:
        instance, image = environment(prior, args.instance)
    except ValueError as exc:
        parser.error(str(exc))
    if args.create and instance:
        parser.error("release.json already names an instance; --create is for a new environment")
    sha = command("git", "rev-parse", "HEAD", cwd=root)
    if command("git", "status", "--porcelain", cwd=root):
        raise RuntimeError("Commit local changes before planning a release")
    remote_sha = command(
        "gh", "api", "repos/" + source_repo + "/commits/" + args.source_ref, "--jq", ".sha"
    )
    if remote_sha != sha:
        raise RuntimeError("Local source differs from selected remote ref")
    args.output = args.output.resolve()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    journal = {
        "schema": "aiab-release-attempt/v1",
        "source_commit": sha,
        "tag": args.tag,
        "instance": instance,
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
        run_id, built_sha = workflow(
            source_repo, "build-image.yml", args.source_ref, args.output, "-f", "image=" + image
        )
        if built_sha != sha:
            raise RuntimeError("Source ref moved during build")
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
            **({"instance": instance} if instance else {}),
            "image": image,
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
        (args.output / "release.json").write_bytes(canonical(release) + b"\n")
        if instance is None and not args.create:
            # A new environment: nothing runs this config yet. The measured release is what
            # the instance gets created from (--create here, once the project secrets exist);
            # its id and hostname then go into release.json as "instance" and "host", and
            # the next release deploys to it.
            record(
                "measured",
                status="succeeded",
                next_step="run again with --create NAME once the project secrets exist",
            )
            print("No instance in release.json: measured only, nothing deployed.", flush=True)
            return
        if instance is None:
            # The environment's first instance, from the release just measured. The project
            # secrets the config names must already exist in Tinfoil, or it will not start.
            record("creating", name=args.create, target_release=release)
            instance, host = create_instance(cp, args.create, repo, args.tag, secret_names(config))
            release.update(instance=instance, host=host)
            (args.output / "release.json").write_bytes(canonical(release) + b"\n")
            record("deploying", target_release=release)
        else:
            record("deploying", target_release=release)
            cp.request(
                "POST", "/api/containers/" + instance + "/update", {"tag": args.tag, "hold": False}
            )
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
