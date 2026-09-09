"""Shared logger and context-formatting helpers.

All package modules log under the single ``ocr_fetch`` logger so consumers can
configure handlers/levels for the whole library in one place.
"""

import logging

logger = logging.getLogger("ocr_fetch")


def format_log_context(**kwargs) -> str:
    """Create a compact key=value context suffix for log messages."""
    parts = [f"{k}={v}" for k, v in kwargs.items() if v is not None]
    return " | " + " ".join(parts) if parts else ""


def log_with_context(level: int, message: str, **kwargs) -> None:
    """Log message with standardized context information."""
    logger.log(level, "%s%s", message, format_log_context(**kwargs))
