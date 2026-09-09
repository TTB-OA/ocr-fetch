"""Extension -> converter registry and the local-file conversion entry point."""

import logging
import os
import time
from collections.abc import Callable

from .converters import (
    convert_doc_to_markdown,
    convert_docx_to_markdown,
    convert_html_to_markdown,
    convert_image_to_markdown,
    convert_pdf_to_markdown,
    convert_pptx_to_markdown,
    convert_spreadsheet_to_markdown,
    convert_txt_to_markdown,
    convert_xml_to_markdown,
)
from .dependencies import collect_system_dependency_status, missing_dependencies_for_extension
from .errors import DEPENDENCY_ERROR_PREFIX, conversion_error, is_conversion_error
from .logging_utils import log_with_context, logger
from .mime import MIME_TO_EXTENSION, normalize_content_type

ConverterFunc = Callable[[str], tuple[str, str]]

# Maps file extension to (default_method_name, converter_callable).
# MIME_TO_EXTENSION supplies the link from MIME type → extension → converter.
CONVERTER_REGISTRY: dict[str, tuple[str, ConverterFunc]] = {
    '.pdf':  ('pypdf_text',       convert_pdf_to_markdown),
    '.html': ('html2text',        convert_html_to_markdown),
    '.htm':  ('html2text',        convert_html_to_markdown),
    '.docx': ('markitdown_docx',  convert_docx_to_markdown),
    '.doc':  ('markitdown_doc',   convert_doc_to_markdown),
    '.pptx': ('markitdown_pptx',  convert_pptx_to_markdown),
    '.txt':  ('plaintext',        convert_txt_to_markdown),
    '.xml':  ('xml_formatted',    convert_xml_to_markdown),
    '.csv':  ('pandas_markdown',  convert_spreadsheet_to_markdown),
    '.xls':  ('pandas_markdown',  convert_spreadsheet_to_markdown),
    '.xlsx': ('pandas_markdown',  convert_spreadsheet_to_markdown),
    '.jpg':  ('pytesseract_ocr',  convert_image_to_markdown),
    '.jpeg': ('pytesseract_ocr',  convert_image_to_markdown),
    '.png':  ('pytesseract_ocr',  convert_image_to_markdown),
    '.bmp':  ('pytesseract_ocr',  convert_image_to_markdown),
}


# Derived lookup tables.
EXTENSION_TO_METHOD: dict[str, str] = {ext: info[0] for ext, info in CONVERTER_REGISTRY.items()}

MIME_TO_METHOD: dict[str, str] = {
    mime: EXTENSION_TO_METHOD.get(ext, 'unknown')
    for mime, ext in MIME_TO_EXTENSION.items()
}


def register_converter(
    extension: str,
    method_name: str,
    converter: ConverterFunc,
    mime_types: list[str] | None = None,
) -> None:
    """Register (or override) a converter for a file extension.

    Lets downstream projects add formats without forking this package.
    """
    if not extension.startswith('.'):
        extension = '.' + extension
    extension = extension.lower()
    CONVERTER_REGISTRY[extension] = (method_name, converter)
    EXTENSION_TO_METHOD[extension] = method_name
    for mime_type in mime_types or []:
        normalized = normalize_content_type(mime_type)
        if normalized:
            MIME_TO_EXTENSION[normalized] = extension
            MIME_TO_METHOD[normalized] = method_name


def get_conversion_function(
    file_ext: str,
    content_type: str | None = None,
) -> ConverterFunc | None:
    """Select a conversion function from the unified registry."""
    if file_ext in CONVERTER_REGISTRY:
        return CONVERTER_REGISTRY[file_ext][1]
    if content_type:
        ext = MIME_TO_EXTENSION.get(content_type)
        if ext and ext in CONVERTER_REGISTRY:
            return CONVERTER_REGISTRY[ext][1]
    return None


def get_parse_method_name(file_ext: str, content_type: str | None = None) -> str:
    """Determine the expected parsing method name for an extension/MIME type."""
    file_ext = (file_ext or '').lower()
    if file_ext in EXTENSION_TO_METHOD:
        return EXTENSION_TO_METHOD[file_ext]
    if content_type:
        ct = normalize_content_type(content_type)
        if ct and ct in MIME_TO_METHOD:
            return MIME_TO_METHOD[ct]
    return 'unknown'


def convert_file_to_markdown(
    file_path: str,
    content_type: str | None = None,
) -> tuple[str, str]:
    """Convert a local file to markdown/text with dependency checks.

    Returns:
        (markdown_content, parse_method) tuple.
    """
    file_ext: str = os.path.splitext(file_path)[1].lower()
    normalized_content_type = normalize_content_type(content_type)

    # Python dependency check
    missing_deps = missing_dependencies_for_extension(file_ext)
    if missing_deps:
        msg = (
            f"{DEPENDENCY_ERROR_PREFIX} Missing required package(s) for {file_ext}: "
            f"{', '.join(missing_deps)}]"
        )
        log_with_context(logging.ERROR, "Missing Python dependencies", file_ext=file_ext, missing=','.join(missing_deps))
        return msg, 'unknown'

    # System dependency check
    sys_status = collect_system_dependency_status(file_ext, normalized_content_type)
    if sys_status['required_missing']:
        msg = (
            f"{DEPENDENCY_ERROR_PREFIX} Missing required system tool(s) for {file_ext}: "
            f"{', '.join(sys_status['required_missing'])}]"
        )
        log_with_context(logging.ERROR, "Missing system dependencies", file_ext=file_ext, missing=','.join(sys_status['required_missing']))
        return msg, 'unknown'
    if sys_status['optional_missing']:
        log_with_context(
            logging.WARNING,
            "Missing optional system tools for fallback paths",
            file_ext=file_ext,
            content_type=normalized_content_type,
            missing=','.join(sys_status['optional_missing']),
        )

    conversion_func = get_conversion_function(file_ext, content_type=normalized_content_type)
    if conversion_func:
        convert_start = time.perf_counter()
        markdown_content, parse_method = conversion_func(file_path)
        elapsed_ms = int((time.perf_counter() - convert_start) * 1000)

        log_with_context(
            logging.INFO, "Completed file conversion",
            file_path=file_path, file_ext=file_ext,
            content_type=normalized_content_type,
            method=parse_method, elapsed_ms=elapsed_ms,
        )
        if is_conversion_error(markdown_content):
            return str(markdown_content), parse_method
        if not str(markdown_content).strip():
            return conversion_error("Converter returned empty content", file_path=file_path, method=parse_method), parse_method
        return markdown_content, parse_method

    logger.warning(
        "Unsupported file type ('%s') or content type ('%s') for %s. Attempting to read as text.",
        file_ext, normalized_content_type, file_path,
    )
    return convert_txt_to_markdown(file_path)
