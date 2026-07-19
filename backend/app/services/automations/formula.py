"""Arithmetic formula evaluator for the `formula` automation function (M15c).

A hand-rolled tokenizer + recursive-descent parser. There is **no `eval`, no
`ast`, and no `compile`** anywhere in this module, and that is the entire point:
the expression text arrives from a recipe that a non-technical user typed in the
builder (after the engine's `{{token}}` pass substitutes field values), so it is
untrusted input on the automation control path. A parser can only ever produce a
number; `eval` could produce anything.

Grammar (lowest to highest precedence):

    expression := term (("+" | "-") term)*
    term       := factor (("*" | "/") factor)*
    factor     := ("-" | "+")? primary
    primary    := NUMBER | "(" expression ")" | "round" "(" expression ("," expression)? ")"

Every failure is a `ValueError` whose message is meant to be read by the office
user in the run's error line — "'pending' is not a number", not a stack trace.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Numbers, operators, parens, commas, and bare words (only `round` is legal, but
# words are tokenized so an unknown one can be named in the error).
_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<number>\d+\.\d+|\d+\.|\.\d+|\d+)
      | (?P<word>[A-Za-z_][A-Za-z_0-9]*)
      | (?P<op>[-+*/(),])
      | (?P<bad>\S)
    )
    """,
    re.VERBOSE,
)

MAX_LENGTH = 500  # a formula longer than this is a mistake, not an expression


@dataclass
class _Token:
    kind: str  # "number" | "word" | "op"
    text: str
    pos: int


def tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        match = _TOKEN_RE.match(text, i)
        if match is None or match.end() == i:
            break
        i = match.end()
        if match.group("bad") is not None:
            raise ValueError(f"'{match.group('bad')}' isn't something I can calculate with.")
        for kind in ("number", "word", "op"):
            value = match.group(kind)
            if value is not None:
                tokens.append(_Token(kind, value, match.start(kind)))
                break
    return tokens


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def next(self) -> _Token | None:
        token = self.peek()
        if token is not None:
            self.i += 1
        return token

    def expect_op(self, op: str) -> None:
        token = self.peek()
        if token is None or token.kind != "op" or token.text != op:
            raise ValueError(f"Expected '{op}' in the formula.")
        self.i += 1

    def parse(self) -> float:
        if not self.tokens:
            raise ValueError("The formula is empty.")
        value = self.expression()
        leftover = self.peek()
        if leftover is not None:
            # The common cause is a missing operator ("2 3") or an extra paren.
            raise ValueError(f"Unexpected '{leftover.text}' in the formula.")
        return value

    def expression(self) -> float:
        value = self.term()
        while True:
            token = self.peek()
            if token is None or token.kind != "op" or token.text not in "+-":
                return value
            self.i += 1
            right = self.term()
            value = value + right if token.text == "+" else value - right

    def term(self) -> float:
        value = self.factor()
        while True:
            token = self.peek()
            if token is None or token.kind != "op" or token.text not in "*/":
                return value
            self.i += 1
            right = self.factor()
            if token.text == "*":
                value = value * right
            else:
                if right == 0:
                    raise ValueError("Division by zero.")
                value = value / right

    def factor(self) -> float:
        token = self.peek()
        if token is not None and token.kind == "op" and token.text in "+-":
            self.i += 1
            value = self.factor()
            return -value if token.text == "-" else value
        return self.primary()

    def primary(self) -> float:
        token = self.next()
        if token is None:
            raise ValueError("The formula ends unexpectedly.")

        if token.kind == "number":
            return float(token.text)

        if token.kind == "op" and token.text == "(":
            value = self.expression()
            self.expect_op(")")
            return value

        if token.kind == "word":
            if token.text != "round":
                # An unsubstituted token or a typed-in field name lands here: the
                # engine renders {{...}} before this runs, so a leftover word means
                # a non-numeric value or a misspelling.
                raise ValueError(f"'{token.text}' is not a number.")
            self.expect_op("(")
            value = self.expression()
            digits = 0
            token_after = self.peek()
            if token_after is not None and token_after.kind == "op" and token_after.text == ",":
                self.i += 1
                digits = int(self.expression())
            self.expect_op(")")
            if digits < 0 or digits > 10:
                raise ValueError("round() digits must be between 0 and 10.")
            return round(value, digits)

        raise ValueError(f"Unexpected '{token.text}' in the formula.")


def evaluate(text: str) -> float:
    """Evaluate an arithmetic expression to a float. Raises ValueError with a
    plain-language message on any malformed or non-numeric input."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The formula is empty.")
    if len(text) > MAX_LENGTH:
        raise ValueError(f"The formula is too long (limit {MAX_LENGTH} characters).")

    value = _Parser(tokenize(text)).parse()

    # Overflow (1e308 * 10) and 0/0-style NaN can survive the arithmetic above;
    # neither is a usable result to branch a condition on.
    if math.isnan(value) or math.isinf(value):
        raise ValueError("The formula didn't produce a usable number.")
    return value
