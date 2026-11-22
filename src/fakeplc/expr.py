"""A small, safe expression language for simulated signals and logic rules.

Expressions are parsed with :mod:`ast` and evaluated by walking the tree, so
nothing is ever passed to :func:`eval`. Only arithmetic, comparisons, boolean
operators, conditional expressions, tag names and a fixed set of functions
are allowed.
"""

from __future__ import annotations

import ast
import math
import operator
import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["FUNCTIONS", "Context", "Expression", "ExpressionError"]


class ExpressionError(ValueError):
    """Raised when an expression is not valid or cannot be evaluated."""


@dataclass
class Context:
    """What an expression can see while it is evaluated."""

    values: Mapping[str, Any]
    t: float
    rng: random.Random


def _sine(ctx: Context, amplitude: float = 1.0, period: float = 10.0, offset: float = 0.0) -> float:
    return offset + amplitude * math.sin(2 * math.pi * ctx.t / period)


def _ramp(ctx: Context, low: float = 0.0, high: float = 100.0, period: float = 10.0) -> float:
    return low + (high - low) * ((ctx.t % period) / period)


def _square(ctx: Context, low: float = 0.0, high: float = 1.0, period: float = 10.0) -> float:
    return high if (ctx.t % period) < period / 2 else low


def _noise(ctx: Context, center: float = 0.0, spread: float = 1.0) -> float:
    return center + ctx.rng.uniform(-spread, spread)


def _counter(ctx: Context, step: float = 1, period: float = 1.0) -> float:
    return math.floor(ctx.t / period + 1e-9) * step


def _limit(ctx: Context, value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _plain(func: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(ctx: Context, *args: Any) -> Any:
        return func(*args)

    return wrapper


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sine": _sine,
    "ramp": _ramp,
    "square": _square,
    "noise": _noise,
    "counter": _counter,
    "limit": _limit,
    "min": _plain(min),
    "max": _plain(max),
    "abs": _plain(abs),
    "round": _plain(round),
    "int": _plain(int),
    "float": _plain(float),
    "bool": _plain(bool),
}

_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}

_CMPOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _dotted(node: ast.expr) -> str | None:
    """Return ``a.b.c`` for a chain of attribute accesses on a name."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class Expression:
    """A parsed expression that can be evaluated many times."""

    def __init__(self, source: str) -> None:
        self.source = source
        try:
            tree = ast.parse(source.strip(), mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"invalid expression {source!r}: {exc.msg}") from None
        self._tree = tree.body
        self.names: set[str] = set()
        self._check(self._tree)

    def __repr__(self) -> str:
        return f"Expression({self.source!r})"

    def _check(self, node: ast.expr) -> None:
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, bool)):
                raise ExpressionError(f"only numbers and booleans are allowed in {self.source!r}")
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = _dotted(node)
            if name is None or any(part.startswith("__") for part in name.split(".")):
                raise ExpressionError(f"unsupported attribute access in {self.source!r}")
            self.names.add(name)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                self._check(value)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BINOPS:
                raise ExpressionError(f"operator not allowed in {self.source!r}")
            self._check(node.left)
            self._check(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
                raise ExpressionError(f"operator not allowed in {self.source!r}")
            self._check(node.operand)
        elif isinstance(node, ast.Compare):
            if any(type(op) not in _CMPOPS for op in node.ops):
                raise ExpressionError(f"comparison not allowed in {self.source!r}")
            self._check(node.left)
            for comparator in node.comparators:
                self._check(comparator)
        elif isinstance(node, ast.IfExp):
            self._check(node.test)
            self._check(node.body)
            self._check(node.orelse)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                allowed = ", ".join(sorted(FUNCTIONS))
                raise ExpressionError(f"unknown function in {self.source!r}; allowed: {allowed}")
            for arg in node.args:
                if isinstance(arg, ast.Starred):
                    raise ExpressionError(f"*args are not allowed in {self.source!r}")
                self._check(arg)
            for keyword in node.keywords:
                if keyword.arg is None:
                    raise ExpressionError(f"**kwargs are not allowed in {self.source!r}")
                self._check(keyword.value)
        else:
            raise ExpressionError(f"{type(node).__name__} is not allowed in {self.source!r}")

    def check_names(self, known: Iterable[str]) -> None:
        """Raise if the expression uses a name that is not a tag or ``t``."""
        allowed = set(known) | {"t"}
        unknown = sorted(self.names - allowed)
        if unknown:
            raise ExpressionError(f"unknown tag {unknown[0]!r} in {self.source!r}")

    def evaluate(self, ctx: Context) -> Any:
        try:
            return self._eval(self._tree, ctx)
        except ExpressionError:
            raise
        except Exception as exc:
            raise ExpressionError(f"error evaluating {self.source!r}: {exc}") from exc

    def _eval(self, node: ast.expr, ctx: Context) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = _dotted(node)
            if name == "t":
                return ctx.t
            try:
                return ctx.values[name]  # type: ignore[index]
            except KeyError:
                raise ExpressionError(f"unknown tag {name!r} in {self.source!r}") from None
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for value in node.values:
                    result = self._eval(value, ctx)
                    if not result:
                        return result
                return result
            result = False
            for value in node.values:
                result = self._eval(value, ctx)
                if result:
                    return result
            return result
        if isinstance(node, ast.BinOp):
            return _BINOPS[type(node.op)](self._eval(node.left, ctx), self._eval(node.right, ctx))
        if isinstance(node, ast.UnaryOp):
            operand = self._eval(node.operand, ctx)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.USub):
                return -operand
            return +operand
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, ctx)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, ctx)
                if not _CMPOPS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test, ctx) else node.orelse, ctx)
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)
            args = [self._eval(arg, ctx) for arg in node.args]
            kwargs = {kw.arg: self._eval(kw.value, ctx) for kw in node.keywords if kw.arg}
            return FUNCTIONS[node.func.id](ctx, *args, **kwargs)
        raise ExpressionError(f"{type(node).__name__} is not allowed in {self.source!r}")
