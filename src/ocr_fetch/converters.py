"""Per-format converters producing ``(content, method_name)`` tuples.

Each converter returns a marker string from :mod:`ocr_fetch.errors` instead of
raising, so a caller can distinguish a failure from empty-but-valid output.
"""

import os
import re
import shutil
import warnings
import xml.etree.ElementTree as StdET
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

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
# OCR tuning
# ---------------------------------------------------------------------------
# OCR dominates the wall-clock cost of parsing a scanned PDF: every page is
# rasterized by Poppler and then read by Tesseract, both of which cost roughly a
# second per page. The right trade-off depends on the machine, so these knobs
# are environment-tunable:
#
# - PDF_OCR_DPI: rasterization resolution. 300 is Tesseract's recommended
#   sweet spot; 200 is roughly twice as fast and usually still accurate on
#   typewritten text.
# - PDF_OCR_RENDER_BATCH: pages rendered per Poppler invocation. Each call
#   re-parses the PDF and spawns a process, so batching removes per-page
#   overhead; the batch is what bounds peak memory.
# - PDF_OCR_WORKERS: pages OCR'd concurrently. Tesseract runs as a subprocess,
#   so threads here genuinely overlap work.
#
# Resolved on first use rather than at import, because host applications
# typically load their .env after importing this package.

_OCR_SETTINGS: dict[str, int] | None = None


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer tuning value from the environment."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %d.", name, raw, default)
        return default


def _ocr_settings() -> dict[str, int]:
    """Resolve (and remember) the OCR tuning values."""
    global _OCR_SETTINGS
    if _OCR_SETTINGS is None:
        settings = {
            'dpi': _env_int('PDF_OCR_DPI', 300, minimum=72),
            'render_batch': _env_int('PDF_OCR_RENDER_BATCH', 8),
            'workers': _env_int('PDF_OCR_WORKERS', min(4, os.cpu_count() or 1)),
        }
        if settings['workers'] > 1:
            # Tesseract's internal OpenMP parallelism fights the page-level
            # thread pool for cores; one thread per process is faster here.
            os.environ.setdefault('OMP_THREAD_LIMIT', '1')
        _OCR_SETTINGS = settings
    return _OCR_SETTINGS


# The quality check is a heuristic; scanning megabytes of text to reach the
# same verdict as the first few pages would cost more than it is worth.
TEXT_QUALITY_SAMPLE_CHARS = 20_000


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

    sample = stripped[:TEXT_QUALITY_SAMPLE_CHARS]

    total = len(sample)
    printable = 0
    alnum = 0
    for c in sample:
        if c.isprintable():
            printable += 1
        if c.isalnum():
            alnum += 1

    words = re.findall(r"[A-Za-z0-9]{3,}", sample)
    unique_words = len({w.lower() for w in words})

    printable_ratio = printable / total
    alnum_ratio = alnum / total
    unique_ratio = unique_words / len(words) if words else 0.0

    return printable_ratio >= 0.9 and alnum_ratio >= 0.3 and unique_ratio >= 0.2


def _preprocess_image_for_ocr(image: Image.Image) -> Image.Image:
    """Apply lightweight preprocessing to improve OCR quality."""
    # Pages rendered by Poppler in grayscale mode are already 'L'; skip the copy.
    grayscale = image if image.mode == 'L' else image.convert('L')
    try:
        normalized = ImageOps.autocontrast(grayscale)
    except Exception:
        if grayscale is not image:
            grayscale.close()
        raise
    if normalized is not grayscale and grayscale is not image:
        grayscale.close()
    result = normalized.point(lambda px: 255 if px > 180 else 0)  # type: ignore[operator]
    if result is not normalized and normalized is not image:
        normalized.close()
    return result


def _ocr_image(image: Image.Image) -> str:
    """Preprocess and OCR one already-rendered page image."""
    prepared = _preprocess_image_for_ocr(image)
    try:
        return pytesseract.image_to_string(prepared)
    finally:
        if prepared is not image:
            prepared.close()


def _page_render_batches(pages: list[int], batch_size: int) -> Iterator[list[int]]:
    """Group sorted page numbers into contiguous runs of at most *batch_size*."""
    batch: list[int] = []
    for page in pages:
        if batch and (page != batch[-1] + 1 or len(batch) >= batch_size):
            yield batch
            batch = []
        batch.append(page)
    if batch:
        yield batch


def _pdf_page_count(file_path: str) -> int:
    """Page count from Poppler, falling back to a single page when unknown."""
    try:
        total_pages = int(pdfinfo_from_path(file_path).get('Pages', 0))
    except Exception as e:
        logger.debug("Could not read page count for %s: %s", file_path, e)
        total_pages = 0
    return total_pages if total_pages > 0 else 1


def _ocr_pdf_pages(file_path: str, page_numbers: list[int] | None = None) -> dict[int, str]:
    """OCR selected pages of a PDF and return ``{page_number: text}``.

    Pages are rendered in contiguous batches because every ``convert_from_path``
    call spawns Poppler and re-parses the file. The pages of a batch are OCR'd
    concurrently since Tesseract runs out-of-process.

    Args:
        page_numbers: 1-based pages to OCR. ``None`` means the whole document.
    """
    if shutil.which('tesseract') is None:
        raise RuntimeError("Missing required system dependency for OCR: tesseract")
    if shutil.which('pdftoppm') is None and shutil.which('pdftocairo') is None:
        raise RuntimeError("Missing required Poppler tools for OCR: pdftoppm/pdftocairo")

    if page_numbers is None:
        pages = list(range(1, _pdf_page_count(file_path) + 1))
    else:
        pages = sorted({p for p in page_numbers if p > 0})

    if not pages:
        return {}

    settings = _ocr_settings()
    dpi, render_batch, workers = settings['dpi'], settings['render_batch'], settings['workers']

    results: dict[int, str] = {}
    for batch in _page_render_batches(pages, render_batch):
        images = convert_from_path(
            file_path,
            dpi=dpi,
            first_page=batch[0],
            last_page=batch[-1],
            grayscale=True,
            thread_count=min(len(batch), workers),
        )
        if not images:
            continue

        try:
            if len(images) == 1 or workers == 1:
                texts = [_ocr_image(image) for image in images]
            else:
                with ThreadPoolExecutor(max_workers=min(len(images), workers)) as pool:
                    texts = list(pool.map(_ocr_image, images))
        finally:
            for image in images:
                image.close()

        for offset, page_text in enumerate(texts):
            results[batch[0] + offset] = page_text

    return results


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


# Control bytes that legitimately appear in text (tab, LF, VT, FF, CR, BS, ESC).
_TEXT_CONTROL_BYTES = frozenset(b'\t\n\x0b\x0c\r\x08\x1b')


def looks_like_text(file_path: str, sample_size: int = 8192) -> bool:
    """Sniff the head of a file for binary content (NUL bytes / control-byte density).

    Mirrors git's heuristic: any NUL in the sample means binary. Raises OSError if
    the file cannot be read.
    """
    with open(file_path, 'rb') as f:
        sample = f.read(sample_size)
    if not sample:
        return True
    if b'\x00' in sample:
        return False
    control = sum(1 for b in sample if b < 0x20 and b not in _TEXT_CONTROL_BYTES)
    return control / len(sample) < 0.05


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
    """Convert a PDF to text, falling back to OCR for scanned documents.

    OCR is only applied to the pages whose embedded text is unreadable, so a
    mostly machine-readable PDF with a few scanned inserts keeps its good text
    and only the bad pages are rasterized.
    """
    try:
        with open(file_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            try:
                page_texts = [page.extract_text() or "" for page in reader.pages]
            except Exception as e:
                if "EI stream not found" in str(e):
                    logger.warning("EI stream not found in %s, switching to OCR.", file_path)
                    page_texts = []
                else:
                    raise

        text = "\n".join(page_texts)

        if _is_text_quality_good(text):
            logger.info("Extracted text from text-based PDF: %s", file_path)
            return text, 'pypdf_text'

        # Page count is unknown when extraction failed, so OCR the whole document.
        pages_needing_ocr: list[int] | None
        if page_texts:
            pages_needing_ocr = [
                index + 1
                for index, page_text in enumerate(page_texts)
                if not _is_text_quality_good(page_text)
            ]
            if not pages_needing_ocr:
                logger.info("PDF text is good page-by-page, skipping OCR: %s", file_path)
                return text, 'pypdf_text'
        else:
            pages_needing_ocr = None

        logger.info(
            "Extracted text quality is low. Performing OCR fallback on %s page(s): %s",
            len(pages_needing_ocr) if pages_needing_ocr is not None else "all", file_path,
        )
        try:
            ocr_pages = _ocr_pdf_pages(file_path, pages_needing_ocr)
        except Exception as ocr_error:
            if text.strip():
                logger.warning("OCR fallback unavailable for %s, returning low-quality text: %s", file_path, ocr_error)
                return text, 'pypdf_text_low_quality'
            raise

        if any(page_text.strip() for page_text in ocr_pages.values()):
            if page_texts:
                merged = [
                    ocr_pages[index + 1] if ocr_pages.get(index + 1, "").strip() else page_text
                    for index, page_text in enumerate(page_texts)
                ]
            else:
                merged = [ocr_pages[page] for page in sorted(ocr_pages)]

            partial = bool(page_texts) and len(ocr_pages) < len(page_texts)
            method = 'pytesseract_ocr_pdf_partial' if partial else 'pytesseract_ocr_pdf'
            logger.info("OCR succeeded on scanned PDF (%s): %s", method, file_path)
            return "\n".join(merged), method

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
            return _ocr_image(image)
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
