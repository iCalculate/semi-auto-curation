from __future__ import annotations

import re


_SI_FACTORS = {
    "": 1.0,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "c": 1e-2,
    "d": 1e-1,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}

_NUMBER_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([fpnumcdkKMGT]?)\s*$"
)


def parse_si_number(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip()
    if not text:
        raise ValueError("Empty numeric value.")
    match = _NUMBER_RE.match(text)
    if not match:
        raise ValueError(
            f"Unsupported numeric format: {value!r}. Supported prefixes: f, p, n, u, m, c, d, k, M, G, T."
        )
    number = float(match.group(1))
    prefix = match.group(2)
    return number * _SI_FACTORS[prefix]
