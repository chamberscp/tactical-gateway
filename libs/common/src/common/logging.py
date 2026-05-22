"""Structured logging configuration.

Uses structlog with a JSON renderer in production and a colored console
renderer in development (controlled by LOG_JSON env var). Every log
entry carries timestamp, level, logger name, and any contextual data
attached via bind/contextvars.

Call configure_logging() exactly once during service startup, before
any other logging happens.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.types import Processor


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure stdlib logging and structlog together.

    - stdlib `logging` is captured and routed through structlog so any
      library that uses logging produces compatible output.
    - In JSON mode, output is one JSON object per line (suitable for
      log aggregators).
    - In console mode, output is colored, pretty-printed, and easier to
      read during local development.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors - run on every log entry regardless of renderer.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # The structlog-specific configuration.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # The stdlib handler that renders entries (whether from structlog
    # or from a library using logging directly).
    renderer: Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processor=renderer,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)

    # Tone down a few chatty libraries.
    for name in ("uvicorn.access", "watchfiles.main"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Convenience factory mirroring structlog.get_logger."""
    return structlog.get_logger(name)
