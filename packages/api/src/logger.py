import logging
import os
import sys

import structlog

_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(format="%(message)s", stream=sys.stdout, level=_LEVEL)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, _LEVEL, logging.INFO)),
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
