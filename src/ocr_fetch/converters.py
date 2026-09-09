"""Per-format converters producing ``(content, method_name)`` tuples.

Each converter returns a marker string from :mod:`ocr_fetch.errors` instead of
raising, so a caller can distinguish a failure from empty-but-valid output.
"""

import os
import re
import shutil
import warnings
import xml.etree.ElementTree as StdET
from collections.abc import Callable

import defusedxml.ElementTree as DefusedET
import docx2txt
import html2text
import pandas as pd
import pypandoc
import pypdf
import pytesseract
from markitdown import MarkItDown
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image, ImageOps

from .errors import conversion_error
from .logging_utils import logger

# Suppress ffmpeg warnings from markitdown since we're not processing video files
warnings.filterwarnings("ignore", message=".*ffmpeg.*")

_markitdown = MarkItDown(enable_plugins=True)


# ---------------------------------------------------------------------------
# Text quality & preprocessing helpers
# ---------------------------------------------------------------------------

def _is_text_quality_good(text: str) -> bool:
    """Heuristic check to avoid trusting low-quality extracted PDF text."""
    if not text:
        return False

    stripped = text.strip()
    if len(stripped) < 25:
        return False

    total = len(stripped)
    printable = 0
    alnum = 0
    for c in stripped:
        if c.isprintable():
            printable += 1
        if c.isalnum():
            alnum += 1

    words = re.findall(r"[A-Za-z0-9]{3,}", stripped)
    unique_words = len(set(w.lower() for w in words))

    printable_ratio = printable / total
    alnum_ratio = alnum / total
    unique_ratio = unique_words / len(words) if words else 0.0

    return printable_ratio >= 0.9 and alnum_ratio >= 0.3 and unique_ratio >= 0.2


def _preprocess_image_for_ocr(image: Image.Image) -> Image.Image:
    """Apply lightweight preprocessing to improve OCR quality."""
    grayscale = image.convert('L')
    try:
        normalized = ImageOps.autocontrast(grayscale)
    except Exception:
        grayscale.close()
        raise
    if normalized is not grayscale:
        grayscale.close()
    result = normalized.point(lambda px: 255 if px > 180 else 0)  # type: ignore[operator]
    if result is not normalized:
        normalized.close()
    return result


def _ocr_pdf_pages(file_path: str) -> str:
    """OCR a PDF page-by-page to avoid loading all pages in memory."""
    if shutil.which('tesseract') is None:
        raise RuntimeError("Missing required system dependency for OCR: tesseract")
    if shutil.which('pdftoppm') is None and shutil.which('pdftocairo') is None:
        raise RuntimeError("Missing required Poppler tools for OCR: pdftoppm/pdftocairo")

    try:
        pdf_info = pdfinfo_from_path(file_path)
        total_pages = int(pdf_info.get('Pages', 0))
    except Exception:
        total_pages = 0

    if total_pages <= 0:
        total_pages = 1

    page_texts: list[str] = []
    for page_num in range(1, total_pages + 1):
        images = convert_from_path(
            file_path, dpi=300,
            first_page=page_num, last_page=page_num, fmt='png',
        )
        if not images:
            continue

        image = images[0]
        try:
            prepared = _preprocess_image_for_ocr(image)
            try:
                page_text = pytesseract.image_to_string(prepared)
            finally:
                prepared.close()
            page_texts.append(page_text)
        finally:
            image.close()

    return "\n".join(page_texts)


def _read_text_with_fallback_encodings(file_path: str, encodings: list[str]) -> str:
    """Read text using fallback encodings."""
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


# ---------------------------------------------------------------------------
# Converter wrapper – eliminates boilerplate in simple converters
# ---------------------------------------------------------------------------

def _run_converter(
    method_name: str,
    file_path: str,
    fn: Callable[[], str],
) -> tuple[str, str]:
    """Run *fn* and return ``(content, method_name)`` with uniform error handling."""
    try:
        result = fn()
        logger.info("Converted via %s: %s", method_name, file_path)
        return result, method_name
    except Exception as e:
        error_tag = f"{method_name}_error"
        return conversion_error(str(e), file_path=file_path, method=error_tag), error_tag


# ---------------------------------------------------------------------------
# Individual converters – each returns (content, method_name)
# ---------------------------------------------------------------------------

def convert_pdf_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert a PDF to text, falling back to OCR for scanned documents."""
    try:
        with open(file_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            try:
                extracted_pages = [page.extract_text() or "" for page in reader.pages]
                text = "\n".join(extracted_pages)
            except Exception as e:
                if "EI stream not found" in str(e):
                    logger.warning("EI stream not found in %s, switching to OCR.", file_path)
                    text = ""
                else:
                    raise

        if _is_text_quality_good(text):
            logger.info("Extracted text from text-based PDF: %s", file_path)
            return text, 'pypdf_text'

        logger.info("Extracted text quality is low. Performing OCR fallback: %s", file_path)
        try:
            ocr_text = _ocr_pdf_pages(file_path)
        except Exception as ocr_error:
            if text.strip():
                logger.warning("OCR fallback unavailable for %s, returning low-quality text: %s", file_path, ocr_error)
                return text, 'pypdf_text_low_quality'
            raise

        if ocr_text.strip():
            logger.info("OCR succeeded on scanned PDF: %s", file_path)
            return ocr_text, 'pytesseract_ocr_pdf'

        logger.warning("OCR returned empty output for %s; returning extracted text fallback.", file_path)
        return text, 'pypdf_text_low_quality'

    except Exception as e:
        return conversion_error(f"Could not process PDF: {e}", file_path=file_path, method='pdf_error'), 'pdf_error'


def convert_html_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert an HTML file to Markdown."""
    def _inner() -> str:
        html_content = _read_text_with_fallback_encodings(file_path, ['utf-8', 'utf-8-sig', 'cp1252'])
        h = html2text.HTML2Text()
        h.ignore_links = False
        return h.handle(html_content)
    return _run_converter('html2text', file_path, _inner)


def convert_docx_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert a DOCX file to Markdown."""
    return _run_converter('markitdown_docx', file_path, lambda: _markitdown.convert(file_path).text_content)


def convert_doc_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert a .doc file to Markdown using multiple fallback methods."""
    if not os.path.exists(file_path):
        return conversion_error("DOC file does not exist", file_path=file_path, method='doc_error'), 'doc_error'

    if os.path.getsize(file_path) == 0:
        return conversion_error("DOC file is empty", file_path=file_path, method='doc_empty'), 'doc_empty'

    # Try markitdown first
    try:
        result = _markitdown.convert(file_path)
        if result and hasattr(result, 'text_content') and result.text_content.strip():
            logger.info("Converted DOC via markitdown: %s", file_path)
            return result.text_content, 'markitdown_doc'
    except Exception as e:
        logger.warning("Markitdown failed for DOC %s: %s", file_path, e)

    # Fallback 1: pypandoc
    try:
        markdown = pypandoc.convert_file(file_path, 'markdown')
        if markdown and markdown.strip():
            logger.info("Converted DOC via pypandoc: %s", file_path)
            return markdown, 'pypandoc_doc'
    except Exception as e:
        logger.warning("Pypandoc failed for DOC %s: %s", file_path, e)

    # Fallback 2: docx2txt
    try:
        text = docx2txt.process(file_path)
        if text and text.strip():
            logger.info("Extracted DOC text via docx2txt: %s", file_path)
            return text, 'docx2txt_doc'
    except Exception as e:
        logger.warning("Docx2txt failed for DOC %s: %s", file_path, e)

    # Fallback 3: binary text extraction
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        for encoding in ['utf-8', 'latin-1', 'cp1252', 'ascii']:
            try:
                decoded = content.decode(encoding, errors='ignore')
                text_content = re.sub(r'[^\x20-\x7E\n\r\t]', '', decoded)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                if len(text_content) > 50:
                    logger.info("Extracted DOC text via binary fallback (%s): %s", encoding, file_path)
                    return text_content, 'binary_text_doc'
            except Exception:
                continue
    except Exception as e:
        logger.warning("Binary fallback failed for DOC %s: %s", file_path, e)

    file_size = os.path.getsize(file_path)
    logger.error("All conversion methods failed for DOC %s (size: %d bytes)", file_path, file_size)
    return (
        conversion_error(
            f"DOC file conversion failed: {os.path.basename(file_path)} - {file_size} bytes",
            file_path=file_path,
            method='doc_failed',
        ),
        'doc_failed',
    )


def convert_pptx_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert a .pptx file to text."""
    return _run_converter('markitdown_pptx', file_path, lambda: _markitdown.convert(file_path).text_content)


def convert_image_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert an image file (JPG, PNG, etc.) to text using OCR."""
    def _inner() -> str:
        with Image.open(file_path) as image:
            prepared = _preprocess_image_for_ocr(image)
            try:
                return pytesseract.image_to_string(prepared)
            finally:
                prepared.close()
    return _run_converter('pytesseract_ocr', file_path, _inner)


def convert_xml_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert an XML file to a formatted Markdown code block."""
    try:
        tree = DefusedET.parse(file_path)
        StdET.indent(tree, space="  ")
        root = tree.getroot()
        xml_str: str = StdET.tostring(root, encoding='unicode')  # type: ignore[arg-type]
        content = f"```xml\n{xml_str}\n```"
        logger.info("Converted XML to Markdown: %s", file_path)
        return content, 'xml_formatted'
    except DefusedET.ParseError:
        logger.warning("Could not parse XML %s, falling back to plain text.", file_path)
        return convert_txt_to_markdown(file_path)
    except Exception as e:
        return conversion_error(f"Could not process XML: {e}", file_path=file_path, method='xml_error'), 'xml_error'


def convert_spreadsheet_to_markdown(file_path: str) -> tuple[str, str]:
    """Convert a spreadsheet file (CSV, XLS, XLSX) to a Markdown table."""
    def _inner() -> str:
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext == '.csv':
            csv_read_error = None
            for encoding in ('utf-8', 'utf-8-sig', 'cp1252', 'latin-1'):
                try:
                    df = pd.read_csv(file_path, encoding=encoding, sep=None, engine='python')
                    break
                except Exception as e:
                    csv_read_error = e
            else:
                raise csv_read_error or RuntimeError("Unable to read CSV")
        elif file_ext == '.xls':
            try:
                df = pd.read_excel(file_path, engine='xlrd')
            except Exception as xlrd_error:
                try:
                    df = pd.read_excel(file_path, engine='openpyxl')
                except Exception:
                    try:
                        with open(file_path, 'rb') as f:
                            head = f.read(4096).lower()
                        if b"<html" in head or b"<!doctype" in head or b"<table" in head:
                            logger.warning("Spreadsheet appears to be HTML; using HTML converter: %s", file_path)
                            content, _ = convert_html_to_markdown(file_path)
                            return content
                    except Exception:
                        pass
                    raise xlrd_error
        elif file_ext == '.xlsx':
            df = pd.read_excel(file_path, engine='openpyxl')
        else:
            return ""
        return df.to_markdown(index=False)
    return _run_converter('pandas_markdown', file_path, _inner)


def convert_txt_to_markdown(file_path: str) -> tuple[str, str]:
    """Read a plain text file and return its content."""
    return _run_converter(
        'plaintext', file_path,
        lambda: _read_text_with_fallback_encodings(file_path, ['utf-8', 'utf-8-sig', 'cp1252', 'latin-1']),
    )
