"""Logging that cannot leak what you said (F3) and only writes once (F13).

Two rules hold everywhere in this codebase:

1. **Transcript text is never passed to an ordinary logger.** Code that wants
   to record what was heard calls :func:`transcript_log`, which routes through
   a dedicated logger that :class:`RedactingFilter` drops by default.
2. **Secrets are scrubbed** from every record as a second line of defence, so
   an accidental ``log.info("key=%s", key)`` cannot write a usable key to disk.

Handlers: one rotating file handler (1 MB x 3) always, plus a console handler
only when running from source. The old shell wrapper that redirected stdout
into the same file has been deleted, so nothing is written twice.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

TRANSCRIPT_LOGGER = "bgassist.transcript"
_TRANSCRIPT_DEBUG_SECONDS = 24 * 60 * 60

# Anything that looks like a bearer token / API key, in any log record.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|sk-ant-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|x-api-key:\s*\S+)",
    re.IGNORECASE,
)


class RedactingFilter(logging.Filter):
    """Drops transcript records and scrubs secrets from everything else.

    ``allow_transcripts_until`` is a wall-clock deadline rather than a boolean
    so the debug toggle switches itself back off after 24 hours (see §6.3).
    """

    def __init__(self, allow_transcripts_until: float = 0.0,
                 clock=time.time) -> None:
        super().__init__()
        self.allow_transcripts_until = float(allow_transcripts_until)
        self.clock = clock

    # -- toggles ---------------------------------------------------------
    def allow_transcripts(self, seconds: float = _TRANSCRIPT_DEBUG_SECONDS) -> None:
        self.allow_transcripts_until = self.clock() + float(seconds)

    def deny_transcripts(self) -> None:
        self.allow_transcripts_until = 0.0

    @property
    def transcripts_allowed(self) -> bool:
        return self.clock() < self.allow_transcripts_until

    # -- logging.Filter --------------------------------------------------
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == TRANSCRIPT_LOGGER or getattr(record, "transcript", False):
            if not self.transcripts_allowed:
                return False
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not crash logging
            return True
        if _SECRET_RE.search(message):
            record.msg = _SECRET_RE.sub("[redacted]", message)
            record.args = ()
        return True


_filter = RedactingFilter()


def redacting_filter() -> RedactingFilter:
    """The process-wide filter, so Preferences can flip the debug toggle."""
    return _filter


def transcript_log(message: str, *args) -> None:
    """Log transcript content. Dropped unless transcript debug is enabled."""
    logging.getLogger(TRANSCRIPT_LOGGER).info(message, *args)


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None,
                  console: Optional[bool] = None,
                  allow_transcripts: bool = False) -> logging.Logger:
    """Configure the root logger. Safe to call more than once."""
    from bgassist.platform import paths

    if log_file is None:
        log_file = paths.log_file()
    if console is None:
        # A frozen bundle has no console to write to; running from source does.
        console = not getattr(sys, "frozen", False)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    if allow_transcripts:
        _filter.allow_transcripts()
    else:
        _filter.deny_transcripts()

    handlers: list[logging.Handler] = []
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_file), maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
        handlers.append(file_handler)
        try:
            os.chmod(log_file, 0o600)
        except OSError:  # pragma: no cover
            pass
    except OSError as exc:  # pragma: no cover - unwritable log dir
        print(f"warning: cannot open log file {log_file}: {exc}", file=sys.stderr)

    if console:
        handlers.append(logging.StreamHandler(sys.stdout))

    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(_filter)
        root.addHandler(handler)

    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Third-party libraries are chatty and faster-whisper logs audio durations.
    for noisy in ("faster_whisper", "urllib3", "numba", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return root
