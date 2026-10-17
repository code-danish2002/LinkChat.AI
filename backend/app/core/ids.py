"""Short, sortable, URL-safe identifiers."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(number: int) -> str:
    if number == 0:
        return "0"
    digits = []
    while number:
        number, rem = divmod(number, 36)
        digits.append(_ALPHABET[rem])
    return "".join(reversed(digits))


def new_id(prefix: str) -> str:
    """`prefix_<time36><random>`: sorts by creation time, safe in URLs."""
    stamp = _base36(int(time.time() * 1000))
    return f"{prefix}_{stamp}{secrets.token_hex(4)}"
