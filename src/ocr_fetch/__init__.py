"""ocr-fetch: download files from URLs and convert documents to Markdown/text.

Handles PDF (text-based and scanned/OCR), DOC/DOCX, PPTX, HTML, XML, images,
spreadsheets (CSV/XLS/XLSX), and plain text.

System dependencies (install separately, and ensure they are on PATH):
- Tesseract-OCR: https://github.com/tesseract-ocr/tesseract (required by pytesseract)
- Poppler: https://poppler.freedesktop.org/ (required by pdf2image)
- Pandoc: https://pandoc.org/ (required by pypandoc)
"""

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
from .downloader import build_download_session, download_and_convert_file
from .errors import CONVERSION_ERROR_PREFIX, DEPENDENCY_ERROR_PREFIX, is_conversion_error
from .filenames import sanitize_filename
from .mime import MIME_TO_EXTENSION
from .registry import (
    EXTENSION_TO_METHOD,
    MIME_TO_METHOD,
    convert_file_to_markdown,
    get_parse_method_name,
    register_converter,
)

__all__ = [
    "CONVERSION_ERROR_PREFIX",
    "DEPENDENCY_ERROR_PREFIX",
    "EXTENSION_TO_METHOD",
    "MIME_TO_EXTENSION",
    "MIME_TO_METHOD",
    "build_download_session",
    "convert_doc_to_markdown",
    "convert_docx_to_markdown",
    "convert_file_to_markdown",
    "convert_html_to_markdown",
    "convert_image_to_markdown",
    "convert_pdf_to_markdown",
    "convert_pptx_to_markdown",
    "convert_spreadsheet_to_markdown",
    "convert_txt_to_markdown",
    "convert_xml_to_markdown",
    "download_and_convert_file",
    "get_parse_method_name",
    "is_conversion_error",
    "register_converter",
    "sanitize_filename",
]
