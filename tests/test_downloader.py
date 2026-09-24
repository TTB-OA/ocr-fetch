"""Filename handling and (optional) live download checks."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from ocr_fetch import (
    DOWNLOAD_HEADERS,
    build_download_session,
    download_and_convert_file,
    sanitize_filename,
)
from ocr_fetch.filenames import extract_filename_from_content_disposition

BODY = b"hello from the test server\n" * 100


class _Handler(BaseHTTPRequestHandler):
    seen_user_agents: ClassVar[list[str]] = []

    def do_GET(self):
        _Handler.seen_user_agents.append(self.headers.get("User-Agent", ""))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        if self.path != "/no-length.txt":
            self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *_args):
        pass


@pytest.fixture
def server_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


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


def test_session_sends_browser_headers():
    with build_download_session() as session:
        assert session.headers["User-Agent"] == DOWNLOAD_HEADERS["User-Agent"]
        assert session.trust_env is False


def test_download_within_limit_converts(server_url, tmp_path):
    _Handler.seen_user_agents.clear()
    content, path, method = download_and_convert_file(f"{server_url}/ok.txt", str(tmp_path), "ok.txt")
    assert method == "plaintext"
    assert content == BODY.decode()
    assert path and os.path.exists(path)
    assert _Handler.seen_user_agents == [DOWNLOAD_HEADERS["User-Agent"]]


def test_download_refused_by_content_length(server_url, tmp_path):
    content, _path, method = download_and_convert_file(
        f"{server_url}/big.txt", str(tmp_path), "big.txt", max_bytes=len(BODY) - 1
    )
    assert content is None and method == "unknown"
    assert not (tmp_path / "big.txt").exists()


def test_download_aborted_mid_stream(server_url, tmp_path):
    content, path, method = download_and_convert_file(
        f"{server_url}/no-length.txt", str(tmp_path), "no-length.txt", max_bytes=100
    )
    assert (content, path, method) == (None, None, "unknown")
    assert not (tmp_path / "no-length.txt").exists()


@pytest.mark.skipif(not os.getenv("TEST_URL"), reason="Set TEST_URL to run a live download check")
def test_live_download(tmp_path):
    url = os.environ["TEST_URL"]
    filename = os.getenv("TEST_FILENAME", "downloaded_file")
    content, raw_path, method = download_and_convert_file(url, str(tmp_path), filename)
    assert content and str(content).strip(), f"method={method} raw_path={raw_path}"
