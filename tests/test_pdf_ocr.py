"""Partial-page PDF OCR and OCR tuning, exercised without Tesseract/Poppler."""

from pathlib import Path

import pytest

from ocr_fetch import converters

GOOD_PAGE = "The quick brown fox jumps over the lazy dog near the riverbank today."


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self) -> str:
        return self._text


def _fake_reader(page_texts: list[str]):
    class _Reader:
        def __init__(self, _stream):
            self.pages = [_FakePage(t) for t in page_texts]
    return _Reader


@pytest.fixture
def pdf_path(tmp_path: Path) -> str:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 placeholder")
    return str(path)


def test_only_bad_pages_are_ocrd(monkeypatch, pdf_path):
    monkeypatch.setattr(converters.pypdf, "PdfReader", _fake_reader([GOOD_PAGE, "", GOOD_PAGE, "\x00" * 400]))
    requested: list[list[int] | None] = []

    def fake_ocr(_path, pages=None):
        requested.append(pages)
        return {p: f"ocr page {p} with enough readable words to pass" for p in pages or []}

    monkeypatch.setattr(converters, "_ocr_pdf_pages", fake_ocr)
    content, method = converters.convert_pdf_to_markdown(pdf_path)

    assert requested == [[2, 4]]
    assert method == "pytesseract_ocr_pdf_partial"
    assert content.split("\n")[0] == GOOD_PAGE
    assert "ocr page 2" in content and "ocr page 4" in content


def test_all_pages_ocrd_reports_full_method(monkeypatch, pdf_path):
    monkeypatch.setattr(converters.pypdf, "PdfReader", _fake_reader(["", ""]))
    monkeypatch.setattr(converters, "_ocr_pdf_pages", lambda _p, pages=None: {p: "scanned text" for p in pages or []})
    _, method = converters.convert_pdf_to_markdown(pdf_path)
    assert method == "pytesseract_ocr_pdf"


def test_good_pages_skip_ocr(monkeypatch, pdf_path):
    monkeypatch.setattr(converters.pypdf, "PdfReader", _fake_reader([GOOD_PAGE]))

    def fail_ocr(*_args, **_kwargs):
        raise AssertionError("OCR should not run")

    monkeypatch.setattr(converters, "_ocr_pdf_pages", fail_ocr)
    _, method = converters.convert_pdf_to_markdown(pdf_path)
    assert method == "pypdf_text"


@pytest.mark.parametrize(
    ("pages", "size", "expected"),
    [
        ([1, 2, 3, 4, 5], 2, [[1, 2], [3, 4], [5]]),
        ([1, 2, 5, 6, 9], 8, [[1, 2], [5, 6], [9]]),
        ([], 4, []),
    ],
)
def test_page_render_batches(pages, size, expected):
    assert list(converters._page_render_batches(pages, size)) == expected


def test_ocr_settings_from_env(monkeypatch):
    monkeypatch.setattr(converters, "_OCR_SETTINGS", None)
    monkeypatch.setenv("PDF_OCR_DPI", "10")
    monkeypatch.setenv("PDF_OCR_RENDER_BATCH", "not-a-number")
    monkeypatch.setenv("PDF_OCR_WORKERS", "1")
    settings = converters._ocr_settings()
    assert settings == {"dpi": 72, "render_batch": 8, "workers": 1}
