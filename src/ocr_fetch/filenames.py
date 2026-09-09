"""Filename derivation and Windows-safe sanitization."""

import os
import re
import unicodedata

WINDOWS_RESERVED_BASE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def extract_filename_from_content_disposition(content_disposition: str) -> str | None:
    """Extract filename from Content-Disposition header if present."""
    if not content_disposition:
        return None

    # RFC 5987 style filename*=UTF-8''...
    filename_star = re.search(
        r"filename\*=([^']*)''([^;\n]+)",
        content_disposition,
        re.IGNORECASE,
    )
    if filename_star:
        return filename_star.group(2).strip().strip('"')

    filename_match = re.search(
        r'filename=(?:["\']?)([^"\';\n]+)(?:["\']?)',
        content_disposition,
        re.IGNORECASE,
    )
    if filename_match:
        return filename_match.group(1).strip()
    return None


def sanitize_filename(name: str, ext_hint: str | None = None, fallback: str = "downloaded_file") -> str:
    """
    Produce a Windows-safe filename:
    - Remove illegal characters: <>:"/\\|?* and control chars
    - Normalize Unicode
    - Avoid trailing spaces/dots
    - Add extension if ext_hint provided and missing
    - Avoid reserved device names
    - Limit length (conservative 200 chars)
    """
    if not name:
        name = fallback

    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = name.replace(":", "-")
    name = name.strip().rstrip(".").rstrip()

    if not name:
        name = fallback

    base, ext = os.path.splitext(name)
    if base.upper() in WINDOWS_RESERVED_BASE_NAMES:
        base = f"{base}_file"
        name = base + ext

    if not os.path.splitext(name)[1] and ext_hint:
        if not ext_hint.startswith("."):
            ext_hint = "." + ext_hint
        name = name + ext_hint

    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:200 - len(ext)] + ext

    if name.endswith((" ", ".")):
        name = name.rstrip(" .") + "_"

    return name
