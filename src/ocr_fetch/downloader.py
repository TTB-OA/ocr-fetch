"""URL download with retry, filename resolution, and conversion to Markdown."""

import logging
import os
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .errors import is_conversion_error
from .filenames import extract_filename_from_content_disposition, sanitize_filename
from .logging_utils import log_with_context, logger
from .mime import MIME_TO_EXTENSION, normalize_content_type
from .registry import convert_file_to_markdown


def build_download_session() -> requests.Session:
    """Create a requests Session with retry logic and no proxy env vars."""
    # Admin machines started throwing errors for invalid PROXY in 2026
    # Set requests to ignore env vars setting proxy in this context
    session = requests.Session()
    session.trust_env = False
    retry = Retry(
        total=3, connect=3, read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def download_and_convert_file(
    url: str,
    raw_download_dir: str,
    file_name_override: str,
) -> tuple[str | None, str | None, str]:
    """Download a file, save it, convert it to Markdown.

    Returns:
        (markdown_content, raw_file_path, parse_method).
        Returns (None, None, 'unknown') on failure.
    """
    raw_file_path = None
    try:
        request_start = time.perf_counter()
        os.makedirs(raw_download_dir, exist_ok=True)

        parsed_url = urlparse(url)
        initial_filename = file_name_override or os.path.basename(parsed_url.path) or "downloaded_file"

        log_with_context(logging.INFO, "Starting download", url=url)

        with build_download_session() as session:
            response = session.get(url, stream=True, timeout=(5, 60), allow_redirects=True)
            response.raise_for_status()

            filename = initial_filename
            content_type = normalize_content_type(response.headers.get('content-type', ''))
            ext_hint = MIME_TO_EXTENSION.get(content_type or '')

            # Resolve a better filename when we only have a generic one
            if filename in ('download', 'downloaded_file') or not filename:
                content_disposition = response.headers.get('content-disposition', '')
                extracted_name = extract_filename_from_content_disposition(content_disposition)
                if extracted_name:
                    filename = extracted_name
                    log_with_context(logging.INFO, "Resolved filename from Content-Disposition", url=url, filename=filename)

                if filename in ('download', 'downloaded_file') or not filename:
                    path_part = (
                        parsed_url.path.strip('/').replace('/', '_')
                        if parsed_url.path.strip('/')
                        else parsed_url.netloc.replace('www.', '').replace('.', '_')
                    )
                    ext = ext_hint or '.txt'
                    filename = f"{path_part}{ext}"
                    log_with_context(logging.INFO, "Generated fallback filename", url=url, filename=filename, content_type=content_type)

            filename_clean = sanitize_filename(os.path.basename(filename), ext_hint=ext_hint)
            raw_file_path = os.path.join(raw_download_dir, filename_clean)

            # Ensure extension is present
            file_ext = os.path.splitext(filename_clean)[1].lower()
            if not file_ext and ext_hint and ext_hint.startswith('.'):
                raw_file_path = os.path.join(raw_download_dir, filename_clean + ext_hint)
                log_with_context(logging.INFO, "Applied extension from content type", url=url, extension=ext_hint, raw_file_path=raw_file_path)

            log_with_context(logging.INFO, "Saving downloaded file", url=url, raw_file_path=raw_file_path)

            content_length_header = response.headers.get('content-length')
            expected_bytes = int(content_length_header) if content_length_header and content_length_header.isdigit() else None
            written_bytes = 0
            with open(raw_file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    f.write(chunk)
                    written_bytes += len(chunk)

        if written_bytes == 0:
            raise RuntimeError(f"Downloaded file is empty: {url}")
        if expected_bytes is not None and expected_bytes != written_bytes:
            log_with_context(logging.WARNING, "Content-Length mismatch", url=url, expected_bytes=expected_bytes, downloaded_bytes=written_bytes)

        download_elapsed_ms = int((time.perf_counter() - request_start) * 1000)
        log_with_context(
            logging.INFO, "Download completed",
            url=url, content_type=content_type,
            raw_file_path=raw_file_path,
            bytes=written_bytes, elapsed_ms=download_elapsed_ms,
        )

        markdown_content, parse_method = convert_file_to_markdown(raw_file_path, content_type=content_type)

        if is_conversion_error(markdown_content):
            log_with_context(logging.WARNING, "Conversion returned error marker", url=url, raw_file_path=raw_file_path, method=parse_method, marker=markdown_content)
            return None, raw_file_path, parse_method

        if not markdown_content or not str(markdown_content).strip():
            log_with_context(logging.WARNING, "Conversion produced empty content", url=url, raw_file_path=raw_file_path, method=parse_method)
            return None, raw_file_path, parse_method

        log_with_context(logging.INFO, "Download + conversion succeeded", url=url, raw_file_path=raw_file_path, method=parse_method, output_chars=len(str(markdown_content)))
        return markdown_content, raw_file_path, parse_method

    except requests.exceptions.RequestException as e:
        logger.error("Failed to download %s: %s", url, e)
        return None, None, 'unknown'
    except Exception as e:
        logger.error("An error occurred while processing %s: %s", url, e)
        return None, raw_file_path, 'unknown'
