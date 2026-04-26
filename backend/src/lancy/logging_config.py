"""
Uvicorn log configuration for Lancy.

Controls:
    LOG_FILE         — path to the log file (default: logs/backend.log at the repo root).
                       Set to "" to disable file logging and rely on stdout/stderr only.
    LOG_MAX_BYTES    — max file size before rotation (default: 10 MB)
    LOG_BACKUP_COUNT — number of rotated files to keep (default: 5)

StreamHandlers (stdout/stderr) are always present so container runtimes and
interactive sessions see log output regardless of whether a file is configured.

Call configure_loguru() once at startup to bridge loguru → stdlib logging so that
loguru output from conversational-toolkit flows through the same handlers as uvicorn.
"""

import logging
import os
from pathlib import Path

# Default log file: logs/backend.log at the repo root (3 levels above this package).
# Overridden by LOG_FILE env var. Docker / systemd deployments can set LOG_FILE=""
# to disable file logging and rely purely on stdout/stderr capture instead.
_DEFAULT_LOG_FILE = str(Path(__file__).parents[3] / "logs" / "backend.log")

_DEFAULT_FMT = "%(asctime)s.%(msecs)03d %(levelprefix)s %(message)s"
_ACCESS_FMT = '%(asctime)s.%(msecs)03d %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 10 * 1024 * 1024))
_LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 5))


def build_log_config() -> dict:
    """Return a uvicorn-compatible log_config dict.

    Adds rotating file handlers when LOG_FILE is set. Both file handlers write
    to the same path; on a single-process server this is safe — each handler
    calls os.stat before every emit to check current file size.
    """
    log_file = os.environ.get("LOG_FILE", _DEFAULT_LOG_FILE).strip()

    formatters = {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": _DEFAULT_FMT,
            "datefmt": _DATE_FMT,
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": _ACCESS_FMT,
            "datefmt": _DATE_FMT,
            "use_colors": None,
        },
    }

    handlers: dict = {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
    }
    default_handlers = ["default"]
    access_handlers = ["access"]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        _rotating = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": log_file,
            "maxBytes": _LOG_MAX_BYTES,
            "backupCount": _LOG_BACKUP_COUNT,
            "encoding": "utf-8",
        }
        handlers["file"] = {**_rotating, "formatter": "default"}
        handlers["file_access"] = {**_rotating, "formatter": "access"}
        default_handlers.append("file")
        access_handlers.append("file_access")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": formatters,
        "handlers": handlers,
        "loggers": {
            # "" is the root logger — captures loguru bridge output and any other
            # library that uses stdlib logging. Uvicorn loggers opt out via propagate=False
            # so their messages don't duplicate here.
            "": {"handlers": default_handlers, "level": "INFO"},
            "uvicorn": {"handlers": default_handlers, "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": access_handlers, "level": "INFO", "propagate": False},
        },
    }


class _LoguruToStdlib(logging.Handler):
    """Forwards loguru log records into Python's standard logging."""

    def emit(self, record: logging.LogRecord) -> None:
        logging.getLogger(record.name).handle(record)


def configure_loguru() -> None:
    """Bridge loguru → stdlib logging.

    Call once at startup (after uvicorn configures its handlers). Replaces
    loguru's default stderr sink so that all loguru output — including messages
    from conversational-toolkit — flows through the same file and stream
    handlers as uvicorn.
    """
    try:
        from loguru import logger as _loguru
    except ImportError:
        return  # loguru not installed, nothing to bridge

    _loguru.remove()  # drop the default stderr sink
    _loguru.add(_LoguruToStdlib(), level="DEBUG", format="{message}")
