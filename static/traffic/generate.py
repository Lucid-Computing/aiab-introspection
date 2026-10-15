"""Send synthetic text with evaluation metadata retained only in this process."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx

Label = Literal["benign", "capabilities"]
Outcome = Literal["success", "policy_block", "classifier_error", "http_error", "transport_error"]
CORPUS_PATHS = tuple(
    Path(__file__).with_name(name)
    for name in ("prompts_benign.jsonl", "prompts_capabilities.jsonl")
)


@dataclass(frozen=True)
class Prompt:
    prompt: str
    label: Label
    category: str | None = None
    split: str = "development"


def load_prompts(paths: tuple[Path, ...] | None = None) -> list[Prompt]:
    """Validate corpus records without including their contents in errors."""
    prompts: list[Prompt] = []
    for path in CORPUS_PATHS if paths is None else paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise ValueError("Cannot read prompt corpus") from None
        for line_number, line in enumerate(lines, 1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or set(record) - {
                    "prompt",
                    "label",
                    "category",
                    "split",
                }:
                    raise ValueError
                prompt, label = record.get("prompt"), record.get("label")
                category, split = record.get("category"), record.get("split", "development")
                if not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError
                if label not in ("benign", "capabilities"):
                    raise ValueError
                if category is not None and (not isinstance(category, str) or not category.strip()):
                    raise ValueError
                if split not in ("development", "held_out"):
                    raise ValueError
                prompts.append(Prompt(prompt, label, category, split))
            except (ValueError, TypeError):
                raise ValueError(f"Invalid corpus record at line {line_number}") from None
    if not prompts:
        raise ValueError("Prompt corpus is empty")
    return prompts


def select_prompts(prompts: list[Prompt], count: int, rng: random.Random) -> list[Prompt]:
    """Shuffle complete corpus cycles, then take a random partial cycle."""
    if not prompts or count <= 0:
        raise ValueError("A nonempty corpus and positive count are required")
    selected: list[Prompt] = []
    while len(selected) < count:
        cycle = list(prompts)
        rng.shuffle(cycle)
        selected.extend(cycle[: count - len(selected)])
    return selected


def validate_url(url: str, insecure: bool) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid endpoint URL") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or port == 0
    ):
        raise ValueError("Endpoint must be an HTTP(S) origin without credentials")
    if insecure and (
        parsed.scheme != "https" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError("--insecure requires HTTPS at a localhost loopback endpoint")
    return url.rstrip("/")


def model_id(client: httpx.Client) -> str:
    try:
        response = client.get("/v1/models")
        response.raise_for_status()
        models = response.json()["data"]
        model = models[0]["id"]
        if not isinstance(model, str) or not model.strip():
            raise ValueError
        return model
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise ValueError("Could not obtain a model from /v1/models") from None


def policy_settings(client: httpx.Client) -> tuple[str, float | None]:
    """Best-effort descriptive settings; these are not verified attestation evidence."""
    try:
        response = client.get("/aiab/manifest")
        response.raise_for_status()
        manifest = response.json()
        mode = manifest["enforcement_mode"]
        rate = manifest["sample_rate"]
        if mode not in ("monitor", "block") or type(rate) not in (int, float):
            raise ValueError
        if not math.isfinite(rate) or not 0 <= rate <= 1:
            raise ValueError
        return mode, float(rate)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return "unknown", None


def response_outcome(response: httpx.Response) -> Outcome:
    if response.is_success:
        return "success"
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        code = None
    if response.status_code == 403 and code == "aiab_blocked":
        return "policy_block"
    if response.status_code == 503 and code == "classifier_unavailable":
        return "classifier_error"
    return "http_error"


@dataclass
class Results:
    outcomes: Counter[Outcome] = field(default_factory=Counter)
    by_label: dict[Label, Counter[Outcome]] = field(
        default_factory=lambda: {"benign": Counter(), "capabilities": Counter()}
    )
    held_out: dict[Label, Counter[Outcome]] = field(
        default_factory=lambda: {"benign": Counter(), "capabilities": Counter()}
    )

    def record(self, prompt: Prompt, outcome: Outcome) -> None:
        self.outcomes[outcome] += 1
        self.by_label[prompt.label][outcome] += 1
        if prompt.split == "held_out":
            self.held_out[prompt.label][outcome] += 1


def generate(client: httpx.Client, prompts: list[Prompt], model: str, rate: float) -> Results:
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("Rate must be a finite positive number")
    results = Results()
    next_start = time.monotonic()
    for prompt in prompts:
        time.sleep(max(0.0, next_start - time.monotonic()))
        next_start = time.monotonic() + 1 / rate
        try:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt.prompt}],
                    "stream": False,
                    "max_tokens": 32,
                },
            )
            outcome = response_outcome(response)
        except httpx.HTTPError:
            outcome = "transport_error"
        results.record(prompt, outcome)
    return results


def summarize(results: Results, mode: str, sample_rate: float | None) -> str:
    total = results.outcomes.total()
    capabilities = results.by_label["capabilities"].total()
    fraction = capabilities / total if total else 0.0
    lines = [
        f"Attempted {total} requests; local labels: {total - capabilities} benign, "
        f"{capabilities} capabilities ({fraction:.1%}).",
        f"Successes: {results.outcomes['success']}; policy blocks (403): "
        f"{results.outcomes['policy_block']}; classifier failures (503): "
        f"{results.outcomes['classifier_error']}; other HTTP failures: "
        f"{results.outcomes['http_error']}; transport failures: "
        f"{results.outcomes['transport_error']}.",
        f"Endpoint mode: {mode} (reported settings are unaudited).",
    ]
    if mode == "monitor" and sample_rate is not None:
        lines.extend(
            [
                f"If all attempts were admitted, expected sampled capabilities requests: "
                f"{fraction:.4f} × {sample_rate:g} × {total} = "
                f"{fraction * sample_rate * total:.2f}.",
                "This estimates sampled labeled requests, not classifier flags. Monitor "
                "decisions are asynchronous and cannot be inferred from HTTP successes.",
            ]
        )
    if mode == "block":
        for name, counts in (("All", results.by_label), ("Held-out", results.held_out)):
            lines.append(
                f"{name} label comparison: benign blocked={counts['benign']['policy_block']}, "
                f"benign succeeded={counts['benign']['success']}; "
                f"capabilities blocked={counts['capabilities']['policy_block']}, "
                f"capabilities succeeded={counts['capabilities']['success']}; "
                f"classifier failures benign={counts['benign']['classifier_error']}, "
                f"capabilities={counts['capabilities']['classifier_error']}."
            )
        lines.append(
            "Blocks reflect the configured confidence threshold. Successes are not proof of "
            "a benign classifier prediction; HTTP/transport failures are not benign predictions."
        )
    lines.append(
        "Labels and held-out categories are local synthetic ground truth. Removing workflow "
        "context can make purpose ambiguous. Request share is not token/compute share or "
        "verified research prevalence; this smoke run does not establish adversarial robustness."
    )
    return "\n".join(lines)


def positive_count(value: str) -> int:
    try:
        count = int(value)
        if count <= 0:
            raise ValueError
        return count
    except ValueError:
        raise argparse.ArgumentTypeError("count must be a positive integer") from None


def positive_rate(value: str) -> float:
    try:
        rate = float(value)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError
        return rate
    except ValueError:
        raise argparse.ArgumentTypeError("rate must be a finite positive number") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://localhost:8443")
    parser.add_argument("--count", type=positive_count, default=200)
    parser.add_argument(
        "--rate", type=positive_rate, default=1.0, help="maximum requests per second"
    )
    parser.add_argument(
        "--insecure", action="store_true", help="trust a local self-signed TLS cert"
    )
    args = parser.parse_args()
    try:
        url = validate_url(args.url, args.insecure)
        prompts = select_prompts(load_prompts(), args.count, random.Random())
        if args.insecure:
            print("WARNING: local TLS certificate verification disabled; no attestation verified.")
        with httpx.Client(
            base_url=url,
            verify=not args.insecure,
            timeout=120,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            model = model_id(client)
            mode, sample_rate = policy_settings(client)
            results = generate(client, prompts, model, args.rate)
        print(summarize(results, mode, sample_rate))
    except ValueError as exc:
        parser.exit(2, f"Traffic generator: {exc}\n")


if __name__ == "__main__":
    main()
