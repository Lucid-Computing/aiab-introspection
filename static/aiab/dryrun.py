"""Evaluate a folder plan; mock mode is explicit and never accepted as final evidence."""

import argparse
import asyncio
from pathlib import Path

from aiab.audit.model import PinnedModel
from aiab.audit.protocol import AuditError, canonical, clone, decode, digest, validate
from aiab.baked.bundle import file_digest, load_bundle
from aiab.baked.executor import execute
from aiab.baked.records import RECORD
from aiab.config import read_private_key
from auditor.audit import write_new
from auditor.evaluate import summarize


def decision_digest(bundle):
    files = dict(bundle.files)
    cfg = clone(bundle.config)
    cfg["classification"].pop("evaluation")
    files["plan.json"] = canonical(cfg)
    return file_digest(files)


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


class MockModel:
    model = {}

    async def check_context(self, text, policy):
        return None

    async def classify(self, text, policy, model):
        terms = ("culture", "primer", "protocol", "iptg", "od600", "assay", "soluble")
        return min(1.0, 0.3 * sum(term in text.lower() for term in terms))


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
            if set(result) != {"flagged"} or type(result["flagged"]) is not bool:
                raise AuditError("invalid_output")
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
        "decision_digest": decision_digest(bundle),
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
