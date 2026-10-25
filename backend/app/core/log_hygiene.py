"""Log hygiene (blueprint §11.2: no document bodies or raw customer PII in logs; redact secrets and tokens).

The app logs IDs, codes and timings, never question or document text. This filter is the safety net for what
other code puts in a message: uvicorn's access log (file-link tokens and search terms live in URLs), exception
messages, and third-party libraries. SQL parameters are already hidden by the engine (`hide_parameters`).
"""

import logging
import re

from app.core.privacy import redact_personal

_SECRETS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bbearer\s+[\w.~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), "[jwt]"),  # session tokens and signed file links
    (re.compile(r"(/files/)[^\s\"'?#]+"), r"\1[token]"),
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@"), r"\1***@"),  # credentials in URLs
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[api-key]"),
    (re.compile(r"(?i)([?&](?:q|question|email|token)=)[^&\s\"]+"), r"\1[redacted]"),
]


def scrub(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return redact_personal(text)


def _clean(value: object) -> object:
    """Numbers and None keep their type (formatters use %d on them); anything else is scrubbed as text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub(str(value))


class RedactingFilter(logging.Filter):
    """Scrubs each record's message template and every argument in place. The arguments stay a tuple (or
    mapping), because some formatters unpack them: uvicorn's access log reads `(client, method, path, …)`.
    Attach it to handlers, so records from every logger pass it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrub(str(record.msg))
        if isinstance(record.args, tuple):
            record.args = tuple(_clean(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _clean(v) for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = scrub(logging.Formatter().formatException(record.exc_info))
        return True


_LOGGERS = ("", "uvicorn", "uvicorn.access", "uvicorn.error")


def install_redaction(level: str = "INFO") -> None:
    """Add the filter to every handler the app and uvicorn log through (idempotent). Under uvicorn the root
    logger has no handler, and app records would fall through to Python's unfiltered last-resort handler, so
    one is added first."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in _LOGGERS:
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())


def configure_logging(level: str) -> None:
    """For processes without uvicorn (the worker and CLIs)."""
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Chroma call drowns the job log
    install_redaction(level)
