"""Keep secrets and prohibited personal records out of the index (blueprint §7.2).

Runs on parsed text before chunking, so a match never reaches Chroma, Postgres chunks or the LLM. Patterns are
high-precision on purpose: a false positive hides a sentence of policy; a false negative leaks a credential.
Only counts by kind are recorded (as a job warning). Matched values are never logged or stored.

Covered: private keys, cloud/API tokens, JWTs, payment card numbers (Luhn-checked), US Social Security
numbers.
Policy (docs/data-model.md): customer records and orders never belong in documents (non-goal, docs/scope.md).
"""

import re
from collections import Counter
from collections.abc import Callable

_PATTERNS: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = []


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in _digits(number)]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _add(kind: str, pattern: str, check: Callable[[str], bool] | None = None, flags: int = 0) -> None:
    _PATTERNS.append((kind, re.compile(pattern, flags), check))


_add(
    "private key",
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)",
    flags=re.DOTALL,
)
_add("access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_add("API key", r"\bsk-(?:proj-|ant-|live_|test_)?[A-Za-z0-9_-]{20,}")
_add("API key", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")
_add("API key", r"\bxox[abprs]-[A-Za-z0-9-]{10,}")
_add("API key", r"\bAIza[0-9A-Za-z_-]{35}\b")
_add("token", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_add("payment card", r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])", luhn_valid)
_add("national ID", r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")


def redact(text: str) -> tuple[str, Counter[str]]:
    found: Counter[str] = Counter()
    for kind, pattern, check in _PATTERNS:

        def replace(
            match: re.Match[str], kind: str = kind, check: Callable[[str], bool] | None = check
        ) -> str:
            if check is not None and not check(match.group(0)):
                return match.group(0)
            found[kind] += 1
            return f"[redacted {kind}]"

        text = pattern.sub(replace, text)
    return text, found
