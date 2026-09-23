"""ZIP archive converter: expand, convert each member via the registry, concatenate.

Guards against zip bombs (member count / size caps) and never uses member names
to build on-disk paths, so path traversal cannot occur.
"""

import logging
import os
import posixpath
import tempfile
import zipfile

from .errors import conversion_error, is_conversion_error
from .filenames import sanitize_filename
from .logging_utils import log_with_context

ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({'.zip'})

MAX_ARCHIVE_MEMBERS = 200
MAX_MEMBER_BYTES = 50 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
# Above this ratio a member is treated as a decompression bomb.
MAX_COMPRESSION_RATIO = 100

_ARCHIVE_METHOD = 'zip_archive'


def _member_path_is_safe(name: str) -> bool:
    """Reject absolute paths, drive letters, and parent-directory components."""
    normalized = name.replace('\\', '/')
    if normalized.startswith('/') or ':' in normalized.split('/')[0]:
        return False
    return '..' not in normalized.split('/')


def _member_skip_reason(info: zipfile.ZipInfo) -> str | None:
    if not _member_path_is_safe(info.filename):
        return 'unsafe path'
    if info.flag_bits & 0x1:
        return 'encrypted'
    if info.file_size > MAX_MEMBER_BYTES:
        return f'member exceeds {MAX_MEMBER_BYTES} bytes'
    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        return 'suspicious compression ratio'
    ext = posixpath.splitext(info.filename)[1].lower()
    if ext in ARCHIVE_EXTENSIONS:
        return 'nested archive'
    return None


def _extract_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest_path: str) -> None:
    """Stream a member to *dest_path*, enforcing the size cap on actual bytes read."""
    written = 0
    with zf.open(info) as src, open(dest_path, 'wb') as dst:
        while chunk := src.read(65536):
            written += len(chunk)
            if written > MAX_MEMBER_BYTES:
                raise ValueError(f"Member exceeded {MAX_MEMBER_BYTES} bytes during extraction")
            dst.write(chunk)


def convert_zip_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert every supported member of a ZIP archive and join them with headers."""
    from .registry import convert_file_to_markdown  # noqa: PLC0415 – avoid import cycle

    try:
        zf = zipfile.ZipFile(file_path)
    except (zipfile.BadZipFile, OSError) as e:
        return conversion_error(f"Could not open ZIP: {e}", file_path=file_path, method=_ARCHIVE_METHOD), _ARCHIVE_METHOD

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            return conversion_error(
                f"ZIP has {len(infos)} members (limit {MAX_ARCHIVE_MEMBERS})",
                file_path=file_path, method=_ARCHIVE_METHOD,
            ), _ARCHIVE_METHOD
        total_declared = sum(i.file_size for i in infos)
        if total_declared > MAX_TOTAL_UNCOMPRESSED_BYTES:
            return conversion_error(
                f"ZIP declares {total_declared} uncompressed bytes (limit {MAX_TOTAL_UNCOMPRESSED_BYTES})",
                file_path=file_path, method=_ARCHIVE_METHOD,
            ), _ARCHIVE_METHOD

        sections: list[str] = [f"# {os.path.basename(file_path)}"]
        converted = 0
        with tempfile.TemporaryDirectory(prefix='ocr_fetch_zip_') as tmpdir:
            for idx, info in enumerate(infos):
                header = f"## {info.filename}"
                skip = _member_skip_reason(info)
                if skip:
                    log_with_context(logging.WARNING, "Skipping ZIP member", archive=file_path, member=info.filename, reason=skip)
                    sections.append(f"{header}\n\n> Skipped: {skip}")
                    continue

                safe_name = sanitize_filename(posixpath.basename(info.filename))
                member_path = os.path.join(tmpdir, f"{idx}_{safe_name}")
                try:
                    _extract_member(zf, info, member_path)
                except Exception as e:
                    log_with_context(logging.WARNING, "Failed to extract ZIP member", archive=file_path, member=info.filename, error=str(e))
                    sections.append(f"{header}\n\n> Extraction failed: {e}")
                    continue

                content, method = convert_file_to_markdown(member_path)
                if is_conversion_error(content):
                    sections.append(f"{header}\n\n> Conversion failed ({method}): {str(content).strip()}")
                    continue
                converted += 1
                sections.append(f"{header}\n\n{str(content).strip()}")

    if converted == 0:
        return conversion_error(
            f"No convertible members in ZIP ({len(infos)} entries)",
            file_path=file_path, method=_ARCHIVE_METHOD,
        ), _ARCHIVE_METHOD

    log_with_context(logging.INFO, "Converted ZIP archive", archive=file_path, members=len(infos), converted=converted)
    return "\n\n".join(sections) + "\n", _ARCHIVE_METHOD
