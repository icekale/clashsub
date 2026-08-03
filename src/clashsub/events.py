from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


TOKEN_PATH = re.compile(r"/(raw|clash|clash-ha|surge|loon|smart)/[^\s?#/]+")
QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+")
HEADER = re.compile(r"(?i)\b(authorization|cookie):\s*[^\r\n]+")
FIELD = re.compile(r"(?i)\b(token|password)=([^&\s]+)")


def redact(value: object) -> str:
    text = str(value)
    text = TOKEN_PATH.sub(lambda match: f"/{match.group(1)}/<redacted>", text)
    text = QUERY.sub(r"\1?<redacted>", text)
    text = HEADER.sub(lambda match: f"{match.group(1)}: <redacted>", text)
    return FIELD.sub(lambda match: f"{match.group(1)}=<redacted>", text)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"clashsub.{name}")


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("clashsub")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved = path.resolve()
    if any(getattr(handler, "_clashsub_path", None) == resolved for handler in logger.handlers):
        return logger
    handler = RotatingFileHandler(
        path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler._clashsub_path = resolved
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def read_recent_events(path: Path, limit: int = 200) -> list[str]:
    if limit <= 0:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return lines[-min(limit, 500) :]
