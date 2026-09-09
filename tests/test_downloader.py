"""Filename handling and (optional) live download checks."""

import os

import pytest

from ocr_fetch import download_and_convert_file, sanitize_filename
from ocr_fetch.filenames import extract_filename_from_content_disposition


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("a/b\\c:d*e?.txt", "a_b_c_d_e_.txt"),
        ("CON.txt", "CON_file.txt"),
        ("trailing.  ", "trailing"),
        ("", "downloaded_file"),
    ],
)
def test_sanitize_filename(raw: str, expected: str):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_applies_ext_hint():
    assert sanitize_filename("report", ext_hint="pdf") == "report.pdf"


def test_sanitize_filename_truncates_long_names():
    result = sanitize_filename("x" * 500 + ".pdf")
    assert len(result) <= 200
    assert result.endswith(".pdf")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ('attachment; filename="ruling.pdf"', "ruling.pdf"),
        ("attachment; filename=ruling.pdf", "ruling.pdf"),
        ("attachment; filename*=UTF-8''w%C3%ADne.pdf", "w%C3%ADne.pdf"),
        ("", None),
        ("inline", None),
    ],
)
def test_extract_filename_from_content_disposition(header: str, expected: str | None):
    assert extract_filename_from_content_disposition(header) == expected


def test_download_bad_url_returns_none():
    content, path, method = download_and_convert_file(
        "http://localhost:1/nope.pdf", "output/test_download", "nope.pdf"
    )
    assert (content, path, method) == (None, None, "unknown")


@pytest.mark.skipif(not os.getenv("TEST_URL"), reason="Set TEST_URL to run a live download check")
def test_live_download(tmp_path):
    url = os.environ["TEST_URL"]
    filename = os.getenv("TEST_FILENAME", "downloaded_file")
    content, raw_path, method = download_and_convert_file(url, str(tmp_path), filename)
    assert content and str(content).strip(), f"method={method} raw_path={raw_path}"
