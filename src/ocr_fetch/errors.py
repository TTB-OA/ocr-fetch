"""Standardized error markers.

Converters never raise to callers; they return a marker string so a failed
conversion can be persisted/inspected alongside successful output.
"""

import logging

from .logging_utils import log_with_context

CONVERSION_ERROR_PREFIX = "[Conversion Error:"
DEPENDENCY_ERROR_PREFIX = "[Dependency Error:"


def conversion_error(reason: str, file_path: str | None = None, method: str | None = None) -> str:
    """Create a standardized conversion error marker string."""
    marker = f"{CONVERSION_ERROR_PREFIX} {reason}]"
    log_with_context(logging.ERROR, "Conversion failed", reason=reason, file_path=file_path, method=method)
    return marker


def is_conversion_error(content: str | None) -> bool:
    """Return True when content is a standardized conversion/dependency error marker."""
    if content is None:
        return True
    normalized = str(content).strip()
    return normalized.startswith(CONVERSION_ERROR_PREFIX) or normalized.startswith(DEPENDENCY_ERROR_PREFIX)
