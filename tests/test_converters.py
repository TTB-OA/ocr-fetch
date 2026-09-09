"""Conversion checks across the bundled fixture documents."""

from pathlib import Path

import pytest
from conftest import fixture_paths, requires_poppler, requires_tesseract

from ocr_fetch import convert_file_to_markdown, get_parse_method_name, is_conversion_error
from ocr_fetch.errors import DEPENDENCY_ERROR_PREFIX


def _assert_converts(path: Path) -> str:
    content, method = convert_file_to_markdown(str(path))
    if str(content).strip().startswith(DEPENDENCY_ERROR_PREFIX):
        pytest.skip(f"Missing dependency for {path.name}: {content}")
    assert not is_conversion_error(content), f"{path.name} -> {method}: {content}"
    assert content.strip(), f"{path.name} -> {method}: empty output"
    return method


@pytest.mark.parametrize("path", fixture_paths(".txt", ".csv", ".htm", ".html", ".xml"), ids=lambda p: p.name)
def test_text_like_fixtures(path: Path):
    _assert_converts(path)


@pytest.mark.parametrize("path", fixture_paths(".xls", ".xlsx"), ids=lambda p: p.name)
def test_spreadsheet_fixtures(path: Path):
    _assert_converts(path)


@pytest.mark.parametrize("path", fixture_paths(".doc", ".docx", ".pptx"), ids=lambda p: p.name)
def test_word_fixtures(path: Path):
    _assert_converts(path)


@requires_tesseract
@requires_poppler
@pytest.mark.parametrize("path", fixture_paths(".pdf"), ids=lambda p: p.name)
def test_pdf_fixtures(path: Path):
    _assert_converts(path)


@requires_tesseract
@pytest.mark.parametrize("path", fixture_paths(".jpg", ".jpeg", ".png", ".bmp"), ids=lambda p: p.name)
def test_image_fixtures(path: Path):
    _assert_converts(path)


def test_unsupported_extension_falls_back_to_text(tmp_path: Path):
    odd = tmp_path / "notes.unknownext"
    odd.write_text("hello world", encoding="utf-8")
    content, method = convert_file_to_markdown(str(odd))
    assert method == "plaintext"
    assert "hello world" in content


def test_missing_file_returns_error_marker(tmp_path: Path):
    content, _ = convert_file_to_markdown(str(tmp_path / "does_not_exist.txt"))
    assert is_conversion_error(content)


@pytest.mark.parametrize(
    ("file_ext", "content_type", "expected"),
    [
        (".pdf", None, "pypdf_text"),
        (".HTM", None, "html2text"),
        ("", "text/csv; charset=utf-8", "pandas_markdown"),
        (".zzz", None, "unknown"),
    ],
)
def test_get_parse_method_name(file_ext: str, content_type: str | None, expected: str):
    assert get_parse_method_name(file_ext, content_type) == expected
