"""Turning Bitrix names and IDs into stable spec keys."""

from __future__ import annotations

import re

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ә": "a", "ғ": "g", "қ": "k",
    "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h",
}  # fmt: skip

MAX_KEY_LENGTH = 48


def slugify(text: str) -> str:
    """'Первичный приём' -> 'pervichnyy_priem'. May return '' for symbol-only input."""
    out = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    out = re.sub(r"[^a-z0-9]+", "_", out).strip("_")
    if out and out[0].isdigit():
        out = f"n{out}"
    return out[:MAX_KEY_LENGTH].rstrip("_")


def is_meaningful_code(code: str) -> bool:
    """False for Bitrix auto-generated codes like '1712345678' or 'UC_X7K2P1'."""
    if not code or code.isdigit():
        return False
    if re.fullmatch(r"UC_[A-Z0-9]{6}", code):
        return False
    # Auto-generated XML_IDs of enumeration values are md5 hashes.
    return not re.fullmatch(r"[0-9a-f]{32}", code)


class KeyAllocator:
    """Hands out unique keys within one scope, suffixing ``_2``, ``_3`` on clashes.

    ``reserved`` maps remote identities (Bitrix IDs) to keys from a previous
    state, so re-importing a portal keeps keys stable even after objects are
    renamed, and new objects never take a key that already means something else.
    """

    def __init__(self, reserved: dict[str, str] | None = None) -> None:
        self._reserved = dict(reserved or {})
        self._used: set[str] = set(self._reserved.values())

    def allocate(self, *candidates: str, fallback: str, remote: object = None) -> str:
        if remote is not None and str(remote) in self._reserved:
            return self._reserved[str(remote)]
        base = next((s for c in candidates if c and (s := slugify(c))), slugify(fallback))
        key, n = base, 2
        while key in self._used:
            suffix = f"_{n}"
            key = base[: MAX_KEY_LENGTH - len(suffix)] + suffix
            n += 1
        self._used.add(key)
        return key
