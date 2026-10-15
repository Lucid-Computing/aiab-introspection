"""Read and edit plan.py as text: one function at a time, checked like the box checks it."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from aiab.baked.bundle import check_source


@dataclass(frozen=True)
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    signature: str
    source: str


def plan_functions(source: str) -> dict[str, FunctionInfo]:
    tree = ast.parse(source, filename="plan.py")
    lines = source.splitlines(keepends=True)
    found: dict[str, FunctionInfo] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        end = node.end_lineno or node.lineno
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        found[node.name] = FunctionInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=end,
            signature=f"def {node.name}({ast.unparse(node.args)}){returns}",
            source="".join(lines[node.lineno - 1 : end]),
        )
    return found


def replace_function_source(source: str, name: str, new_source: str) -> str:
    """Return plan.py with one function replaced; raises if the result would not pass the box."""
    new_tree = ast.parse(new_source, filename=f"{name}.py")
    defs = [n for n in new_tree.body if isinstance(n, ast.FunctionDef)]
    if len(new_tree.body) != 1 or len(defs) != 1 or defs[0].name != name:
        raise ValueError(f"expected exactly one function named {name}")
    info = plan_functions(source).get(name)
    if info is None:
        raise KeyError(name)
    lines = source.splitlines(keepends=True)
    if not new_source.endswith("\n"):
        new_source += "\n"
    updated = "".join(lines[: info.lineno - 1]) + new_source + "".join(lines[info.end_lineno :])
    check_source(updated)  # the same allowlist the box applies at boot (AuditError on failure)
    return updated
