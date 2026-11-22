"""Keep bearer tokens out of every log sink.

Tokens reach log records through URLs: the student/teacher REST endpoints and the
WebSocket handshake carry ``?token=...`` because an ``<audio>`` element and a
browser ``WebSocket`` cannot send headers.  Any logger that formats a request
line (uvicorn's access log, an HTTP client, a future debug line) therefore
writes a working credential to disk unless it is scrubbed.

Two cooperating pieces, because log records arrive in two shapes:

* :class:`RedactionFilter` cleans the format string and each string argument in
  place.  Positions are preserved, so uvicorn's access formatter can still read
  the args tuple to rebuild the request line.
* :class:`RedactingFormatter` wraps a handler's existing formatter and scrubs
  the fully rendered line, which catches ``log.info("token=%s", tok)`` where the
  secret argument is bare and only the template names it.

Redaction is applied to the handlers installed by the app and to uvicorn's own
loggers, so it holds for stdio and for ``data/guru.log`` without disabling
logging.
"""

from __future__ import annotations

import logging
import re
from typing import Any

PLACEHOLDER = "[redacted]"

# Token values never contain a literal "%": excluding it keeps format
# placeholders ("token=%s") intact so a record can still be rendered later.
_VALUE = r"""[^&\s"',;%]+"""

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ?token=abc / &teacher_token=abc / x_teacher_token=abc
    (
        re.compile(
            r"(?i)((?<![A-Za-z0-9])(?:token|teacher_token|student_token|access_token"
            r"|api_key|apikey|password|secret|teacher_pin|pin)=)" + _VALUE
        ),
        r"\1" + PLACEHOLDER,
    ),
    # Authorization: Bearer abc
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 " + PLACEHOLDER),
    # Header dumps: X-Teacher-Token: abc / {'Cookie': 'abc'} / Authorization: abc
    (
        re.compile(
            r"(?i)((?<![A-Za-z0-9])(?:x-teacher-token|x-teacher-pin|authorization"
            r"|cookie|set-cookie)['\"]?\s*[:=]\s*['\"]?)" + _VALUE
        ),
        r"\1" + PLACEHOLDER,
    ),
)

# Loggers that do not propagate to the root handlers (propagate=0), or that get
# new handlers installed after this module runs.  A logger-level filter survives
# uvicorn's dictConfig, which replaces handlers but not filters.
_NON_PROPAGATING = ("uvicorn", "uvicorn.error", "uvicorn.access")


def redact(text: str) -> str:
    """Return ``text`` with token-like values replaced by ``[redacted]``."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_args(args: Any) -> Any:
    """Redact string arguments while keeping the container's shape and length."""
    if isinstance(args, tuple):
        return tuple(redact(a) if isinstance(a, str) else a for a in args)
    if isinstance(args, dict):
        return {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
    return args


class RedactionFilter(logging.Filter):
    """Scrub credentials without disturbing how a record will be formatted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = _redact_args(record.args)
        return True


class RedactingFormatter(logging.Formatter):
    """Render with the handler's own formatter, then redact the final line."""

    def __init__(self, inner: logging.Formatter | None = None) -> None:
        super().__init__()
        self._inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return redact(self._inner.format(record))

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return self._inner.formatTime(record, datefmt)

    def formatException(self, ei: Any) -> str:
        return self._inner.formatException(ei)

    def formatStack(self, stack_info: str) -> str:
        return self._inner.formatStack(stack_info)


def _attach(logger: logging.Logger) -> None:
    if not any(isinstance(f, RedactionFilter) for f in logger.filters):
        logger.addFilter(RedactionFilter())
    for handler in logger.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(RedactionFilter())
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(handler.formatter))


def install_log_redaction() -> None:
    """Idempotently attach redaction to the root logger and to uvicorn's."""
    _attach(logging.getLogger())
    for name in _NON_PROPAGATING:
        _attach(logging.getLogger(name))
