"""Interpret a tiny Python expression language; never compile or exec plan code."""

from __future__ import annotations

import ast
import operator
from typing import Any

from aiab.audit.protocol import AuditError

COMPARE = {
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class Program:
    def __init__(self, source: str):
        try:
            tree = ast.parse(source)
            if len(list(ast.walk(tree))) > 256 or len(tree.body) != 1:
                raise ValueError
            fn = tree.body[0]
            if (
                not isinstance(fn, ast.FunctionDef)
                or fn.name != "run"
                or fn.decorator_list
                or fn.returns
                or fn.type_params
                or [a.arg for a in fn.args.args] != ["text", "model", "policy"]
                or any(a.annotation for a in fn.args.args)
                or fn.args.posonlyargs
                or fn.args.kwonlyargs
                or fn.args.defaults
                or fn.args.vararg
                or fn.args.kwarg
                or fn.args.kw_defaults
            ):
                raise ValueError
            self.body = fn.body
            known = {"text", "model", "policy"}
            calls = 0
            for i, statement in enumerate(self.body):
                if isinstance(statement, ast.Assign):
                    if (
                        len(statement.targets) != 1
                        or not isinstance(statement.targets[0], ast.Name)
                        or statement.targets[0].id in known
                        or statement.targets[0].id.startswith("_")
                    ):
                        raise ValueError
                    calls += self._check(statement.value, known)
                    known.add(statement.targets[0].id)
                elif isinstance(statement, ast.Return) and i == len(self.body) - 1:
                    calls += self._check(statement.value, known)
                else:
                    raise ValueError
            if not self.body or not isinstance(self.body[-1], ast.Return) or calls != 1:
                raise ValueError
        except (ValueError, SyntaxError, TypeError, RecursionError):
            raise AuditError("invalid_program") from None

    def _check(self, node: ast.AST, known: set[str]) -> int:
        if isinstance(node, ast.Constant):
            if type(node.value) not in (str, bool, int, float, type(None)):
                raise ValueError
            return 0
        if isinstance(node, ast.Name) and node.id in known - {"model", "policy"}:
            return 0
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "policy" and node.attr == "threshold":
                return 0
            if node.value.id in known - {"text", "model", "policy"} and node.attr == "score":
                return 0
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Attribute)
                or not isinstance(node.func.value, ast.Name)
                or node.func.value.id != "model"
                or node.func.attr != "classify"
                or node.keywords
                or len(node.args) != 2
                or not all(isinstance(a, ast.Name) for a in node.args)
                or [a.id for a in node.args] != ["text", "policy"]
            ):
                raise ValueError
            return 1
        if isinstance(node, ast.Dict):
            if any(
                not isinstance(k, ast.Constant) or type(k.value) is not str for k in node.keys
            ) or len({k.value for k in node.keys}) != len(node.keys):
                raise ValueError
            return sum(self._check(v, known) for v in node.values)
        if isinstance(node, ast.Compare) and all(type(op) in COMPARE for op in node.ops):
            return sum(self._check(v, known) for v in [node.left, *node.comparators])
        if isinstance(node, ast.IfExp):
            return sum(self._check(v, known) for v in (node.test, node.body, node.orelse))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self._check(node.operand, known)
        raise ValueError

    async def run(self, text: str, policy: dict, classify: Any) -> Any:
        values = {"text": text}
        calls = 0

        async def evaluate(node: ast.AST) -> Any:
            nonlocal calls
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return values[node.id]
            if isinstance(node, ast.Attribute):
                if node.value.id == "policy":
                    return policy["threshold"]
                return values[node.value.id]["score"]
            if isinstance(node, ast.Call):
                calls += 1
                if calls > 1:
                    raise AuditError("execution_failed")
                return {"score": await classify(text, policy)}
            if isinstance(node, ast.Dict):
                return {
                    k.value: await evaluate(v) for k, v in zip(node.keys, node.values, strict=True)
                }
            if isinstance(node, ast.Compare):
                left = await evaluate(node.left)
                for op, right_node in zip(node.ops, node.comparators, strict=True):
                    right = await evaluate(right_node)
                    if not COMPARE[type(op)](left, right):
                        return False
                    left = right
                return True
            if isinstance(node, ast.IfExp):
                return await evaluate(node.body if await evaluate(node.test) else node.orelse)
            if isinstance(node, ast.UnaryOp):
                return not await evaluate(node.operand)
            raise AuditError("execution_failed")

        for statement in self.body:
            if isinstance(statement, ast.Assign):
                values[statement.targets[0].id] = await evaluate(statement.value)
            else:
                result = await evaluate(statement.value)
                if calls != 1:
                    raise AuditError("execution_failed")
                return result
        raise AuditError("execution_failed")
