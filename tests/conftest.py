"""Shared pytest fixtures and system-dependency helpers."""

from pathlib import Path

import pytest

from ocr_fetch.dependencies import is_system_tool_available

FIXTURES_DIR = Path(__file__).parent / "fixtures"

requires_tesseract = pytest.mark.skipif(
    not is_system_tool_available("tesseract"),
    reason="Tesseract-OCR is not installed or not on PATH",
)

requires_poppler = pytest.mark.skipif(
    not is_system_tool_available("pdftoppm|pdftocairo"),
    reason="Poppler (pdftoppm/pdftocairo) is not installed or not on PATH",
)


def fixture_paths(*extensions: str) -> list[Path]:
    """Return fixture files matching the given extensions, sorted by name."""
    wanted = {e.lower() for e in extensions}
    return sorted(p for p in FIXTURES_DIR.iterdir() if p.is_file() and p.suffix.lower() in wanted)
