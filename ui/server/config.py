"""Environment-driven settings for the agreement UI and its backend integrations."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

UI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = UI_ROOT.parent
STATIC_DIR = REPO_ROOT / "static"
TEST_PROXY_URL = (
    "https://47b9b017832cbbf8ca2572b480584460df142e04-8080.dstack-pha-prod9.phala.network"
)
PROD_PROXY_URL = "https://llm-proxy.lucidcompute.com"
DEFAULT_LOG_SOURCE = "dev"


@dataclass(frozen=True)
class LogSource:
    """One logging proxy the Logs and Runs pages can read: production's, the test one, and
    whatever comes later. Each keeps its own folder of segments, index and prefetch loop.
    Adding a variety is adding one entry in ``log_sources``; no page changes."""

    id: str  # in URLs: /logs?logs=prod
    label: str  # on the selector button
    url: str  # the proxy's address (its /_logs download API); "" for a local copy
    header: str  # the header the key is sent in
    key: str | None  # a proxy: nothing is fetched without one; Slack, GitHub: opens the bundle
    key_env: str  # the environment variable the key or folder comes from, for messages
    folder: str  # under settings.logs_dir, unless ``root`` names the folder outright
    kind: str = "proxy"  # "proxy": the logging proxy's JSON lines; "slack", "github": an export;
    # "plant": the RSI plant's exports (server.plantlogs)
    root: Path | None = None  # a folder kept elsewhere (the Slack export, a static copy)

    def path(self, settings: Settings) -> Path:
        return self.root if self.root is not None else settings.logs_dir / self.folder

    @property
    def configured(self) -> bool:
        """Whether the pages can offer this source: a proxy needs its key; an export (Slack,
        GitHub) needs its folder named, or the key that opens the committed bundle."""
        return bool(self.key) or self.root is not None

    @property
    def chat(self) -> bool:
        """Whether the records are posts people wrote (an export), not calls a model answered."""
        return self.kind in ("slack", "github")

    @property
    def fetches(self) -> bool:
        """Whether this server downloads the source itself (Refresh, the prefetch thread)."""
        return self.kind == "proxy" and bool(self.key)


@dataclass(frozen=True)
class Settings:
    plan_dir: Path
    agreements_dir: Path
    git_dir: Path
    deploy_base: str | None
    trust_file: Path
    box_url: str | None
    accounts_file: Path
    session_secret: bytes
    repo: str | None
    runs_dir: Path  # one folder per audit run started from the Runs screen
    demo_keys_dir: Path  # lab.token and logger.key for the live box, never in the repo
    release_file: Path  # the release pins the live box must match
    push_drafts: bool  # push agreement folders to the checkout's branch after each save
    auto_pull: bool  # fast-forward the checkout to its remote branch every minute when clean
    logs_dir: Path  # downloaded proxy log segments, one subfolder per log source
    log_sources: tuple[LogSource, ...]  # Prod, Dev, Slack, GitHub, in selector order
    # The Slack export, committed sealed (server.slackexport); opened into logs_dir/slack at
    # start when AIAB_SLACK_EXPORT_KEY is set and no export folder is named.
    slack_bundle: Path = UI_ROOT / "data" / "slack-export.enc"
    # The GitHub export (server.githubexport), the same way: AIAB_GITHUB_EXPORT_KEY opens it
    # into logs_dir/github unless AIAB_GITHUB_EXPORT names a folder.
    github_bundle: Path = UI_ROOT / "data" / "github-export.enc"

    anthropic_api_key: str | None = field(default=None, repr=False)
    # AIAB_LAB_KEY: the lab's signing key (base64 raw Ed25519, the format of lab.key from
    # make dev-keys) and AIAB_LAB_CERTIFICATE: the certificate the lab's root gave it (the
    # JSON line in lab.cert). Both set, the Sign screen offers Lab Sign, a demo-only button
    # that signs the shown version as the lab with this key, and every page says DEV MODE;
    # anyone logged in can then sign as the lab. The lab's root stays with the lab.
    # Prototype only.
    lab_signing_key: str | None = field(default=None, repr=False)
    lab_signing_certificate: str | None = None
    # AIAB_AUDITOR_ROOT_KEY: the auditor root's private key (base64 raw Ed25519). Set, the
    # server certifies the signing key each logged-in auditor's browser makes on its first
    # Sign, so every auditor has a key of their own and nobody sets anything up. Prototype
    # only: whoever controls the server can then certify a key and sign as the auditor
    # (THREAT_MODEL.md, "Prototype shortcut").
    auditor_root_key: str | None = field(default=None, repr=False)
    # OpenID Connect login (WorkOS AuthKit; any provider with a discovery document). Issuer,
    # client id and secret set: Sign in goes through the provider; emails in admins_file are
    # the lab, everyone else is the auditor. oidc_group set: the ID token's groups claim must
    # name it too (Okta; needs the groups scope). Local accounts in accounts_file work either way.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = field(default=None, repr=False)
    oidc_provider_name: str = "WorkOS"  # what the Sign in button and error messages call it
    oidc_group: str | None = None  # empty: no group check; anyone the provider signs in
    oidc_groups_claim: str = "groups"
    oidc_scopes: str = "openid profile email"
    admins_file: Path = UI_ROOT / "admins.json"  # {"admin": [emails]}: the lab
    # Signatures are checked against production's trust file as GitHub serves it, the same
    # file the plan check and the next release use; the copy on disk stands in only when
    # GitHub has not answered. Off when AIAB_TRUST_FILE names a dev trust file.
    trust_follows_production: bool = False
    # Feedback from the site: the form's entries and the post-it notes, one JSONL file each
    # (server.feedback). AIAB_FEEDBACK_WEBHOOK, a Slack incoming webhook, also gets each one.
    feedback_dir: Path = UI_ROOT / "feedback"
    feedback_webhook: str | None = field(default=None, repr=False)

    @property
    def repo_url(self) -> str | None:
        return f"https://github.com/{self.repo}" if self.repo else None

    def log_source(self, wanted: str | None) -> LogSource:
        """The log source a page asked for by id, or the default (Dev Agent Logs) when the
        id is missing or unknown."""
        by_id = {s.id: s for s in self.log_sources}
        return by_id.get(wanted or "") or by_id.get(DEFAULT_LOG_SOURCE) or self.log_sources[0]

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)


def _repo_from_git() -> str | None:
    try:
        url = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    for prefix in ("git@github.com:", "https://github.com/"):
        if url.startswith(prefix):
            return url.removeprefix(prefix).removesuffix(".git")
    return None


def _load_dotenv(path: Path) -> None:
    """ui/.env, if present: KEY=VALUE lines, applied only where the environment is unset.

    Secrets for a laptop run live there, git-ignored; on Render the same names are set in
    the service's environment and this file does not exist.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def load_settings() -> Settings:
    _load_dotenv(UI_ROOT / ".env")
    env = os.environ
    secret = env.get("UI_SESSION_SECRET")
    release_file = Path(
        env.get("AIAB_RELEASE_FILE", STATIC_DIR / "deploy/tinfoil/audit/release.json")
    ).resolve()
    box_url = env.get("AIAB_BOX_URL", "").rstrip("/")
    if not box_url and release_file.is_file():
        box_url = "https://" + json.loads(release_file.read_text())["host"]
    repo = env.get("AIAB_REPO") or _repo_from_git()
    return Settings(
        # The folder a PlanView reads; for_agreement() points it at one agreement.
        plan_dir=Path(env.get("AIAB_PLAN_DIR", REPO_ROOT / "plan")).resolve(),
        agreements_dir=Path(env.get("AIAB_AGREEMENTS_DIR", REPO_ROOT / "agreements")).resolve(),
        git_dir=Path(env.get("AIAB_GIT_DIR", REPO_ROOT)).resolve(),
        deploy_base=env.get("AIAB_DEPLOY_BASE") or "production",  # the branch the box is built from
        # The keys signatures are checked against: the checkout's trust.json, i.e. the one on
        # the deploy branch, the same file the release bakes into the box. Never edited
        # here; a key changes through a pull request. AIAB_TRUST_FILE overrides for dev keys.
        trust_file=Path(
            env.get("AIAB_TRUST_FILE")
            or Path(env.get("AIAB_GIT_DIR", REPO_ROOT)) / "static" / "aiab" / "baked" / "trust.json"
        ).resolve(),
        box_url=box_url or None,
        accounts_file=Path(env.get("UI_ACCOUNTS_FILE", UI_ROOT / "accounts.json")).resolve(),
        session_secret=secret.encode("utf-8") if secret else secrets.token_bytes(32),
        repo=repo,
        trust_follows_production=bool(repo) and not env.get("AIAB_TRUST_FILE"),
        runs_dir=Path(env.get("AIAB_RUNS_DIR", UI_ROOT / "runs")).resolve(),
        demo_keys_dir=Path(
            env.get("AIAB_DEMO_KEYS", Path.home() / ".local/share/aiab-audit-demo")
        ).resolve(),
        release_file=release_file,
        push_drafts=env.get("AIAB_PUSH_DRAFTS", "").lower() in {"1", "true", "yes"},
        auto_pull=env.get("AIAB_AUTO_PULL", "").lower() in {"1", "true", "yes"},
        logs_dir=Path(env.get("AIAB_LOGS_DIR", UI_ROOT / "logs")).resolve(),
        log_sources=log_sources_from_env(env),
        slack_bundle=Path(
            env.get("AIAB_SLACK_BUNDLE", UI_ROOT / "data" / "slack-export.enc")
        ).resolve(),
        github_bundle=Path(
            env.get("AIAB_GITHUB_BUNDLE", UI_ROOT / "data" / "github-export.enc")
        ).resolve(),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip() or None,
        lab_signing_key=env.get("AIAB_LAB_KEY", "").strip() or None,
        lab_signing_certificate=env.get("AIAB_LAB_CERTIFICATE", "").strip() or None,
        auditor_root_key=env.get("AIAB_AUDITOR_ROOT_KEY", "").strip() or None,
        oidc_issuer=env.get("OIDC_ISSUER", "").strip().rstrip("/") or None,
        oidc_client_id=env.get("OIDC_CLIENT_ID", "").strip() or None,
        oidc_client_secret=env.get("OIDC_CLIENT_SECRET", "").strip() or None,
        oidc_provider_name=env.get("OIDC_PROVIDER_NAME", "WorkOS").strip() or "WorkOS",
        oidc_group=env.get("OIDC_GROUP", "").strip() or None,
        oidc_groups_claim=env.get("OIDC_GROUPS_CLAIM", "groups").strip(),
        oidc_scopes=env.get("OIDC_SCOPES", "openid profile email").strip(),
        admins_file=Path(env.get("UI_ADMINS_FILE", UI_ROOT / "admins.json")).resolve(),
        feedback_dir=Path(env.get("AIAB_FEEDBACK_DIR", UI_ROOT / "feedback")).resolve(),
        feedback_webhook=env.get("AIAB_FEEDBACK_WEBHOOK", "").strip() or None,
    )


def log_sources_from_env(env: Mapping[str, str]) -> tuple[LogSource, ...]:
    """The log sources this server reads, all at once when all are set. Production takes
    its reader key as x-log-key (AIAB_LOG_KEY); the test proxy takes its shared key as
    x-proxy-key (AIAB_PROXY_KEY); each has its own folder under AIAB_LOGS_DIR. Slack Logs
    is the public-channel export, a static copy: read in place from the folder
    AIAB_SLACK_EXPORT names, or, without one, opened from the committed bundle with
    AIAB_SLACK_EXPORT_KEY into AIAB_LOGS_DIR/slack at start (server.slackexport). GitHub
    Logs, the organisation's public repositories, works the same way with AIAB_GITHUB_EXPORT
    and AIAB_GITHUB_EXPORT_KEY (server.githubexport). Plant Logs, the RSI plant's exports,
    is read in place from the folder AIAB_PLANT_EXPORT names (server.plantlogs), never
    from git."""
    slack = env.get("AIAB_SLACK_EXPORT", "").strip()
    slack_key = env.get("AIAB_SLACK_EXPORT_KEY", "").strip() or None
    github = env.get("AIAB_GITHUB_EXPORT", "").strip()
    github_key = env.get("AIAB_GITHUB_EXPORT_KEY", "").strip() or None
    plant = env.get("AIAB_PLANT_EXPORT", "").strip()
    return (
        LogSource(
            id="prod",
            label="Prod Agent Logs",
            url=env.get("AIAB_LOG_URL", PROD_PROXY_URL).rstrip("/"),
            header="x-log-key",
            key=env.get("AIAB_LOG_KEY", "").strip() or None,
            key_env="AIAB_LOG_KEY",
            folder="prod",
        ),
        LogSource(
            id="dev",
            label="Dev Agent Logs",
            url=env.get("AIAB_PROXY_URL", TEST_PROXY_URL).rstrip("/"),
            header="x-proxy-key",
            key=_proxy_key(env.get("AIAB_PROXY_KEY", "")),
            key_env="AIAB_PROXY_KEY",
            folder="dev",
        ),
        LogSource(
            id="slack",
            label="Slack Logs",
            url="",
            header="",
            key=None if slack else slack_key,
            key_env="AIAB_SLACK_EXPORT" if slack else "AIAB_SLACK_EXPORT_KEY",
            folder="slack",
            kind="slack",
            root=Path(slack).resolve() if slack else None,
        ),
        LogSource(
            id="github",
            label="GitHub Logs",
            url="",
            header="",
            key=None if github else github_key,
            key_env="AIAB_GITHUB_EXPORT" if github else "AIAB_GITHUB_EXPORT_KEY",
            folder="github",
            kind="github",
            root=Path(github).resolve() if github else None,
        ),
        LogSource(
            id="plant",
            label="Plant Logs",
            url="",
            header="",
            key=None,
            key_env="AIAB_PLANT_EXPORT",
            folder="plant",
            kind="plant",
            root=Path(plant).resolve() if plant else None,
        ),
    )


def _proxy_key(value: str) -> str | None:
    """The key as pasted from 1Password: either bare, or a "name:key,name:key" list, in which
    case the "tester" entry (or the first) is used."""
    value = value.strip()
    if not value:
        return None
    if ":" not in value:
        return value
    pairs = dict(p.strip().split(":", 1) for p in value.split(",") if ":" in p)
    return pairs.get("tester") or next(iter(pairs.values()), None)
