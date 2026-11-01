"""Evaluate a folder plan; mock mode is explicit and never accepted as final evidence."""

import argparse
import asyncio
import re
from pathlib import Path

from aiab.audit.model import PinnedModel
from aiab.audit.protocol import AuditError, canonical, decode, digest, validate
from aiab.baked.bundle import file_digest, load_bundle
from aiab.baked.executor import execute
from aiab.baked.records import RECORD
from aiab.config import read_private_key
from auditor.audit import write_new
from auditor.evaluate import summarize


def dataset(folder):
    files = {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted((folder / "tests/transcripts").glob("*.jsonl"))
    }
    files["tests/expected.json"] = (folder / "tests/expected.json").read_bytes()
    expected = decode(files["tests/expected.json"])
    if not 2 <= len(files) <= 101 or set(expected) != {
        Path(k).name for k in files if k.endswith(".jsonl")
    }:
        raise AuditError("invalid_dataset")
    return files, expected


STOP = {"about", "their", "there", "these", "this", "that", "with", "from", "into", "other"}


def stems(text):
    return {w[:5] for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOP}


class MockModel:
    """Development-only stand-in for the pinned model: deterministic, keyword-based, and
    never accepted as evaluation evidence. A classifier gets the label whose name and
    description overlap most with the text; a free-form step gets its input echoed."""

    model = {}

    async def check_messages(self, content, max_output_tokens):
        return None

    async def complete(self, content, schema, max_output_tokens):
        props = schema["properties"]
        user = content[-1]["content"]
        if "label" in props:
            labels = props["label"]["enum"]
            words = stems(user)
            # The prompt the box built (prompts.py): a description line per label, then the
            # plan's examples: the flagged labels' examples, then the others'.
            lines = content[0]["content"].splitlines()
            evidence = {label: set() for label in labels}
            flagged, section = [], None
            for line in lines:
                if line.startswith("Labels"):
                    section = "labels"
                elif line.startswith("Examples that should get"):
                    section = "positive"
                    flagged = [lab for lab in labels if lab in line.split(":")[0]]
                elif line.startswith("Examples that should not"):
                    section = "negative"
                elif line.startswith("- ") and section:
                    if section == "labels":
                        label = line[2:].split(":")[0]
                        if label in evidence:
                            evidence[label] |= stems(line.replace("_", " "))
                    else:
                        targets = (
                            flagged
                            if section == "positive"
                            else [lab for lab in labels if lab not in flagged]
                        )
                        for label in targets:
                            evidence[label] |= stems(line)
            hits = {label: len(evidence[label] & words) for label in labels}
            best = max(labels, key=lambda lab: (hits[lab], -labels.index(lab)))
            score = min(1.0, 0.5 + 0.1 * hits[best]) if hits[best] > 0 else 0.3
            return {"label": best, "score": round(score, 3)}
        if set(props) == {"text"}:
            return {"text": "[mock summary] " + user[-2000:]}
        fill = {"boolean": False, "integer": 0, "number": 0.0, "string": "mock"}
        return {k: fill[v["type"]] for k, v in props.items()}


async def evaluate(folder, trust, model, mode):
    bundle = load_bundle(folder, trust, signed=False)
    files, expected = dataset(folder)
    cases = []
    for name, raw in sorted(files.items()):
        if not name.endswith(".jsonl"):
            continue
        label = expected[Path(name).name]["flagged"]
        if type(label) is not bool:
            raise AuditError("invalid_dataset")
        records = [decode(line) for line in raw.splitlines() if line.strip()]
        for record in records:
            validate(record, RECORD, "invalid_dataset")
        case = {
            "case_digest": digest({"name": name, "records": records}),
            "expected": {"flagged": label},
            "outcome": "errors",
        }
        try:
            result = await execute(bundle, records, model)
            if type(result.get("flagged")) is not bool:
                raise AuditError("invalid_output")  # the dataset judges one boolean, flagged
            case.update(
                result=result,
                outcome=("t" if result["flagged"] == label else "f")
                + ("p" if result["flagged"] else "n"),
            )
        except AuditError as exc:
            case["error"] = exc.code
        cases.append(case)
    return {
        "schema": "aiab-folder-evaluation/v1",
        "mode": mode,
        "plan_digest": bundle.digest,
        "test_set_digest": file_digest(files),
        "cases": cases,
        **summarize(cases),
    }


async def run(args):
    trust = decode(args.trust.read_bytes())
    model = (
        MockModel()
        if args.mock
        else PinnedModel(
            trust["model"], read_private_key(str(args.credential_file), "TINFOIL_API_KEY")
        )
    )
    try:
        if not args.mock:
            await model.start()
        result = await evaluate(
            args.folder, trust, model, "mock" if args.mock else "attested-inference"
        )
        if args.output:
            write_new(args.output, canonical(result) + b"\n")
        print(canonical(result).decode())
        return int(result["counts"]["errors"] != 0)
    finally:
        if not args.mock:
            await model.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--trust", type=Path, default=Path(__file__).parent / "baked/trust.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mock", action="store_true")
    group.add_argument("--credential-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception as exc:
        raise SystemExit(exc.code if isinstance(exc, AuditError) else "evaluation_failed") from None


if __name__ == "__main__":
    main()
