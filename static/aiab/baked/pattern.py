"""The regular expressions a Filter's "matches regex" rule may use, and how the box runs them.

One small grammar, checked token by token, so the builder (ui/playground/src/plan/pattern.ts,
the same rules in TypeScript) and the box accept exactly the same patterns and read them the
same way. A pattern is never handed to ``re`` as written: it is parsed here and rewritten into
plain Python syntax, so engine differences (what ``\\s`` covers, where ``$`` matches) cannot
change what a signed plan means.

Allowed: literal characters; ``.`` (any character, line breaks too); the escapes ``\\d \\w \\s``
(ASCII digits, word characters, whitespace) and ``\\D \\W \\S`` outside brackets; ``\\t \\n \\r
\\f \\v``; a backslash before any punctuation for that character; bracket sets ``[...]`` and
``[^...]`` with ranges; groups ``(...)`` and ``(?:...)``; ``|``; ``^`` and ``$`` (start and end
of the whole text); the repeats ``* + ? {n} {n,} {n,m}`` (n and m up to 1000), each optionally
lazy. Refused: back-references, look-around, named groups, inline flags, possessive repeats,
characters outside the Basic Multilingual Plane, and a repeated group that holds another
repeat or a ``|``: the nested forms whose matching time grows exponentially.

Only whether the pattern occurs anywhere in the text is used, which lets the box drop the
repeats at either end that cannot change that (``_trim``). Matching runs on Python's
backtracking engine, which has no time limit of its own: an admitted pattern can still be
slow on a long text (``[a-z]{20,}x`` over a long run of letters). The worker's CPU limit stops
a run that never finishes, as it does for Code blocks, which may import ``re`` too.
"""

from __future__ import annotations

import re

MAX_LENGTH = 512
MAX_REPEAT = 1000
PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
CONTROL = {"t": "\t", "n": "\n", "r": "\r", "f": "\f", "v": "\v"}
NAMES = {"\t": "\\t", "\n": "\\n", "\r": "\\r", "\f": "\\f", "\v": "\\v"}
# Character classes, ASCII only, as bracket contents.
CLASSES = {"d": "0-9", "w": "A-Za-z0-9_", "s": " \\t\\n\\r\\f\\v"}


class PatternError(ValueError):
    pass


def _char(c: str) -> str:
    """One literal character in the pattern language's own syntax."""
    if c in NAMES:
        return NAMES[c]
    if c in PUNCT:
        return "\\" + c
    if ord(c) < 0x20 or ord(c) == 0x7F:
        return f"\\x{ord(c):02x}"
    return c


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.depth = 0  # groups open around the current position

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.text[j] if j < len(self.text) else ""

    def fail(self, why: str, at: int | None = None) -> PatternError:
        return PatternError(f"{why} (at character {(self.i if at is None else at) + 1})")

    def alternation(self) -> tuple[str, bool]:
        """(source, holds a repeat or a |)"""
        parts, risky = self.sequence()
        branches = [parts]
        while self.peek() == "|":
            self.i += 1
            more, _ = self.sequence()
            branches.append(more)
            risky = True
        if self.depth == 0 and len(branches) == 1:
            branches = [_trim(parts)]
        return "|".join("".join(a + q for a, q, _, _ in b) for b in branches), risky

    def sequence(self) -> tuple[list[tuple[str, str, int, int | None]], bool]:
        """The pieces in order, each (atom, repeat, least, most), and whether any repeats."""
        out, risky = [], False
        while self.peek() and self.peek() not in "|)":
            atom, inner, kind = self.atom()
            piece = (atom, "", 1, 1)
            if self.peek() and self.peek() in "*+?{":
                if kind == "anchor":
                    raise self.fail("^ and $ cannot be repeated")
                at = self.i
                quant, least, most = self.quantifier()
                if kind == "group" and inner and (most is None or most > 1):
                    raise self.fail("a repeated group cannot hold another repeat or a |", at)
                piece = (atom, quant, least, most)
                inner = True
            out.append(piece)
            risky = risky or inner
        return out, risky

    def quantifier(self) -> tuple[str, int, int | None]:
        c = self.peek()
        if c in "*+?":
            self.i += 1
            text, least, most = c, (1 if c == "+" else 0), (1 if c == "?" else None)
        else:
            m = re.match(r"\{([0-9]{1,4})(,([0-9]{0,4}))?\}", self.text[self.i :])
            if not m:
                raise self.fail("write \\{ for a literal {")
            least = int(m.group(1))
            most = least if m.group(2) is None else (int(m.group(3)) if m.group(3) else None)
            if least > MAX_REPEAT or (most is not None and most > MAX_REPEAT):
                raise self.fail(f"a repeat count is at most {MAX_REPEAT}")
            if most is not None and most < least:
                raise self.fail("in {n,m}, n is at most m")
            self.i += len(m.group(0))
            text = m.group(0)
        if self.peek() == "?":
            self.i += 1
            text += "?"
        if self.peek() and self.peek() in "*+?{":
            raise self.fail("a repeat cannot be repeated")
        return text, least, most

    def atom(self) -> tuple[str, bool, str]:
        """(source, holds a repeat or a |, kind: char, group or anchor)"""
        c = self.peek()
        if c == "(":
            self.i += 1
            open_ = "("
            if self.peek() == "?":
                if self.peek(1) != ":":
                    raise self.fail("the only (? group is (?:...)")
                self.i += 2
                open_ = "(?:"
            self.depth += 1
            source, risky = self.alternation()
            self.depth -= 1
            if self.peek() != ")":
                raise self.fail("missing )")
            self.i += 1
            return open_ + source + ")", risky, "group"
        if c == ")":
            raise self.fail("unbalanced )")
        if c == "[":
            return self.bracket(), False, "char"
        if c in "*+?":
            raise self.fail("nothing to repeat")
        if c in "{}]":
            raise self.fail(f"write \\{c} for a literal {c}")
        self.i += 1
        if c == ".":
            return ".", False, "char"
        if c == "^":
            return "^", False, "anchor"
        if c == "$":
            # The end of the text, as $ is in JavaScript; Python's $ also matches before a
            # final line break.
            return "\\Z", False, "anchor"
        if c == "\\":
            e = self.escape()
            if e in CLASSES:
                return f"[{CLASSES[e]}]", False, "char"
            if e.lower() in CLASSES and e.isupper():
                return f"[^{CLASSES[e.lower()]}]", False, "char"
            return _char(e), False, "char"
        return _char(c), False, "char"

    def escape(self) -> str:
        """The character after a backslash: a class letter, or the literal it stands for."""
        c = self.peek()
        if not c:
            raise self.fail("the pattern ends with \\")
        self.i += 1
        if c in "dwsDWS":
            return c
        if c in CONTROL:
            return CONTROL[c]
        if c in PUNCT:
            return c
        raise self.fail(f"\\{c} is not supported")

    def bracket(self) -> str:
        self.i += 1
        out = "["
        if self.peek() == "^":
            self.i += 1
            out += "^"
        first = True
        while self.peek() != "]":
            if not self.peek():
                raise self.fail("missing ]")
            if self.peek() == "[":
                raise self.fail("write \\[ inside [...]")
            if self.peek() == "-" and not first and self.peek(1) != "]":
                raise self.fail("write \\- for a literal - inside [...]")
            lo, is_class = self.member()
            if is_class:
                out += CLASSES[lo]
            elif self.peek() == "-" and self.peek(1) not in ("]", ""):
                self.i += 1
                hi, hi_class = self.member()
                if hi_class:
                    raise self.fail("a range ends in one character")
                if ord(hi) < ord(lo):
                    raise self.fail("a range runs from low to high")
                out += _char(lo) + "-" + _char(hi)
            else:
                out += _char(lo)
            first = False
        if first:
            raise self.fail("[] is empty")
        self.i += 1
        return out + "]"

    def member(self) -> tuple[str, bool]:
        """One member of a bracket set: (the character, False) or (a class letter, True)."""
        c = self.peek()
        self.i += 1
        if c != "\\":
            return c, False
        e = self.escape()
        if e in CLASSES:
            return e, True
        if e in "DWS":
            raise self.fail(f"\\{e} cannot go inside [...]; use [^...]")
        return e, False


def _trim(parts: list[tuple[str, str, int, int | None]]) -> list[tuple[str, str, int, int | None]]:
    """Only whether the pattern occurs in the text is used, so a repeat at either end of the
    whole pattern that may match nothing is dropped, and one with no upper bound stops at its
    least count: ``.*key`` searches as ``key``, ``\\w+token`` as ``\\wtoken``. The text has a match
    either way; the search does far less backtracking. Not done under a top-level ``|``."""
    parts = list(parts)
    while parts and parts[0][1] and parts[0][2] == 0:
        parts.pop(0)
    while parts and parts[-1][1] and parts[-1][2] == 0:
        parts.pop()
    for k in (0, -1):
        if parts and parts[k][1] and parts[k][3] is None:
            atom, _, least, _ = parts[k]
            parts[k] = (atom, f"{{{least}}}", least, least)
    return parts


def translate(pattern: str) -> str:
    """The pattern rewritten for Python's ``re`` (with DOTALL; ``$`` is the end of the text).
    Raises PatternError with the reason a pattern is refused."""
    if not isinstance(pattern, str) or not pattern:
        raise PatternError("the pattern is empty")
    if any(ord(c) > 0xFFFF for c in pattern):
        raise PatternError("characters outside the Basic Multilingual Plane are not supported")
    if len(pattern) > MAX_LENGTH:
        raise PatternError(f"a pattern is at most {MAX_LENGTH} characters")
    parser = _Parser(pattern)
    source, _ = parser.alternation()
    if parser.i != len(pattern):
        raise parser.fail("unbalanced )")
    return source


def pattern_issue(pattern: str) -> str | None:
    """Why a pattern is refused, or None."""
    try:
        compile_pattern(pattern, True)
    except PatternError as exc:
        return str(exc)
    return None


def compile_pattern(pattern: str, case_sensitive: bool) -> re.Pattern:
    try:
        return re.compile(translate(pattern), re.DOTALL | (0 if case_sensitive else re.IGNORECASE))
    except re.error as exc:  # not expected for an admitted pattern: refuse it all the same
        raise PatternError(f"not a valid pattern: {exc.msg}") from None
